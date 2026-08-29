"""Document management service with improved error handling."""

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import MAX_UPLOAD_SIZE
from ..domain.entities import is_canonical_entity_code
from ..logging_config import get_logger
from ..models import Document, Project
from ..security import read_file_limited, validate_file_content
from .jobs import enqueue_parse
from .metadata import infer_from_filename, load_canonical_entity_cache
from .storage import (
    SAFE_EXTS,
    PathTraversalError,
    StorageError,
    _validate_safe_path,
    save_original,
    sha256_bytes,
)

logger = get_logger(__name__)


def register_bytes(
    db: Session, project: Project, filename: str, data: bytes, metadata: dict | None = None, auto_parse: bool = True
) -> tuple[Document, int | None]:
    """Register a document from bytes with deduplication.

    Args:
        db: Database session
        project: Parent project
        filename: Original filename
        data: File content bytes
        metadata: Optional metadata overrides
        auto_parse: Whether to automatically queue for parsing

    Returns:
        Tuple of (Document, job_id or None)

    Raises:
        StorageError: If file cannot be saved
        PathTraversalError: If path escapes safe directories
    """
    metadata = metadata or {}
    validate_file_content(filename or "", data)
    digest = sha256_bytes(data)

    # Check for duplicate
    duplicate = db.scalar(
        select(Document).where(
            Document.project_id == project.id, Document.file_hash == digest, Document.duplicate_of_id.is_(None)
        )
    )

    # Infer metadata from the same canonical Entity master used by the
    # runtime UI/sync path.  Load it once per registration and pass the
    # explicit rows through inference so a filename containing a real code,
    # company name, or tax id is resolved consistently without opening a
    # session for each individual reference.  If the runtime DB is
    # unavailable, the loader returns an empty cache and inference remains
    # unresolved rather than assigning a default entity.
    inferred = infer_from_filename(
        filename,
        canonical_cache=load_canonical_entity_cache(),
    )

    # Generate document code
    code = f"DOC-{uuid.uuid4().hex[:12].upper()}"

    # Save original file
    try:
        path = save_original(project.project_code, code, filename or code, data)
    except (StorageError, PathTraversalError) as e:
        logger.error(f"Failed to save file: {e}")
        raise

    # Create document record
    d = Document(
        project_id=project.id,
        document_code=code,
        filename=filename or code,
        file_type=Path(filename).suffix.lower().lstrip("."),
        file_hash=digest,
        size_bytes=len(data),
        original_path=str(path),
        duplicate_of_id=duplicate.id if duplicate else None,
        parse_status="DUPLICATE" if duplicate else "UPLOADED",
        # Metadata with priority: explicit > inferred
        document_type=metadata.get("document_type") or inferred.get("document_type", "other"),
        entity_code=metadata.get("entity_code") or inferred.get("entity_code", ""),
        counterparty_code=metadata.get("counterparty_code") or inferred.get("counterparty_code", ""),
        business_category=metadata.get("business_category") or inferred.get("business_category", ""),
        tax_category=metadata.get("tax_category") or inferred.get("tax_category", ""),
        # 税务金额字段
        tax_vat_rate=metadata.get("tax_vat_rate", 0.0) or 0.0,
        tax_vat_input=metadata.get("tax_vat_input", 0.0) or 0.0,
        tax_vat_output=metadata.get("tax_vat_output", 0.0) or 0.0,
        tax_vat_paid=metadata.get("tax_vat_paid", 0.0) or 0.0,
        tax_income_rate=metadata.get("tax_income_rate", 0.0) or 0.0,
        tax_income_amount=metadata.get("tax_income_amount", 0.0) or 0.0,
        tax_income_paid=metadata.get("tax_income_paid", 0.0) or 0.0,
        tax_individual_rate=metadata.get("tax_individual_rate", 0.0) or 0.0,
        tax_individual_amount=metadata.get("tax_individual_amount", 0.0) or 0.0,
        tax_individual_paid=metadata.get("tax_individual_paid", 0.0) or 0.0,
        tax_surtax_urban=metadata.get("tax_surtax_urban", 0.0) or 0.0,
        tax_surtax_edu=metadata.get("tax_surtax_edu", 0.0) or 0.0,
        tax_surtax_local_edu=metadata.get("tax_surtax_local_edu", 0.0) or 0.0,
        tax_stamp_duty=metadata.get("tax_stamp_duty", 0.0) or 0.0,
        tax_land=metadata.get("tax_land", 0.0) or 0.0,
        tax_environmental=metadata.get("tax_environmental", 0.0) or 0.0,
        invoice_no=metadata.get("invoice_no", "") or "",
        invoice_code=metadata.get("invoice_code", "") or "",
        invoice_type=metadata.get("invoice_type", "") or "",
        invoice_date=metadata.get("invoice_date", "") or "",
        invoice_deductible=metadata.get("invoice_deductible", False) or False,
        tax_total=metadata.get("tax_total", 0.0) or 0.0,
        contract_no=metadata.get("contract_no", "") or "",
        period=metadata.get("period") or inferred.get("period", ""),
        metadata_confidence=float(inferred.get("confidence", 0.25)),
        metadata_source="filename",
    )

    db.add(d)
    db.commit()
    db.refresh(d)

    logger.info(
        f"Registered document {d.document_code} for project {project.project_code}{' (duplicate)' if duplicate else ''}"
    )

    # Auto-register external party if counterparty is system-external
    if d.counterparty_code and not is_canonical_entity_code(d.counterparty_code):
        kind = "partner"
        cp_upper = d.counterparty_code.upper()
        if "CRANE" in cp_upper or d.business_category == "equipment":
            kind = "equipment"
        elif "PG" in cp_upper or d.business_category == "material":
            kind = "supplier"
        elif "EXP" in cp_upper or d.business_category == "subcontract":
            kind = "subcontractor"
        elif d.business_category == "labor":
            kind = "labor"
            
        from .ingest import _auto_register_external_party
        _auto_register_external_party(
            db,
            d.counterparty_code,
            counterparty_name=inferred.get("counterparty_name") or d.counterparty_code,
            tax_id=inferred.get("counterparty_tax_id") or None,
            kind=kind,
        )
        db.commit()

    # Queue for parsing
    jid = None
    if auto_parse and not duplicate:
        job = enqueue_parse(db, d.id)
        jid = job.id
        db.refresh(d)

    # Tax and RAG share PostgreSQL; no cross-database async copy is performed.

    return d, jid


def scan_folder(
    db: Session, project: Project, folder: str, recursive: bool = True, auto_parse: bool = True
) -> list[dict]:
    """Scan a folder and import supported files.

    Args:
        db: Database session
        project: Parent project
        folder: Folder path to scan
        recursive: Whether to scan subdirectories
        auto_parse: Whether to queue files for parsing

    Returns:
        List of import results

    Raises:
        ValueError: If folder doesn't exist
    """
    candidate_root = Path(folder).expanduser()

    # Security: validate before resolving so symlinked roots are rejected,
    # then use the canonical path for the actual traversal.
    try:
        root = _validate_safe_path(candidate_root, operation="folder scan")
    except (StorageError, PathTraversalError) as e:
        raise ValueError(f"Folder not accessible: {e}")

    if not root.exists() or not root.is_dir():
        raise ValueError(f"Folder does not exist: {folder}")

    # Security: validate folder is accessible
    try:
        list(root.iterdir())
    except PermissionError:
        raise ValueError(f"Permission denied: {folder}")

    globber = root.rglob("*") if recursive else root.glob("*")
    out = []

    for path in globber:
        if path.is_symlink() or not path.is_file():
            continue

        if path.suffix.lower() not in SAFE_EXTS:
            continue

        try:
            if path.stat().st_size > MAX_UPLOAD_SIZE:
                raise ValueError("file exceeds configured upload size")
            d, jid = register_bytes(db, project, path.name, read_file_limited(path), {}, auto_parse=auto_parse)
            out.append(
                {
                    "source": str(path),
                    "document_id": d.id,
                    "document_code": d.document_code,
                    "status": d.parse_status,
                    "job_id": jid,
                    "is_duplicate": d.duplicate_of_id is not None,
                }
            )
        except (StorageError, PathTraversalError, OSError, ValueError) as e:
            out.append({"source": str(path), "error": str(e), "error_type": type(e).__name__})
            logger.error(f"Failed to import {path}: {e}")

    logger.info(f"Folder scan complete: {len(out)} files, {sum(1 for r in out if 'error' not in r)} imported")

    return out


def register_local_path(
    db: Session, project: Project, filepath: Path, metadata: dict | None = None, auto_parse: bool = True
) -> tuple[Document, int | None]:
    """Register a document directly from a trusted local path.

    Local imports use the exact same bounded read, extension, magic-byte and
    archive validation as HTTP uploads.  The source file is not copied, but
    its canonical path is checked before it is opened and symlinked files are
    rejected.
    """
    metadata = metadata or {}
    filepath = Path(filepath).expanduser()
    if filepath.is_symlink():
        raise PathTraversalError("local import cannot be a symlink")
    try:
        resolved_path = _validate_safe_path(filepath, operation="local import")
    except (StorageError, PathTraversalError):
        raise
    if not resolved_path.is_file():
        raise StorageError(f"local import is not a regular file: {filepath}")
    if resolved_path.suffix.lower() not in SAFE_EXTS:
        raise StorageError(f"Unsupported file type: {resolved_path.suffix or '(none)'}")

    # read_file_limited performs bounded reads plus extension/magic/ZIP checks.
    data = read_file_limited(resolved_path)
    filename = resolved_path.name
    digest = sha256_bytes(data)

    # Check for duplicate
    duplicate = db.scalar(
        select(Document).where(
            Document.project_id == project.id, Document.file_hash == digest, Document.duplicate_of_id.is_(None)
        )
    )

    inferred = infer_from_filename(
        filename,
        canonical_cache=load_canonical_entity_cache(),
    )
    code = f"DOC-{uuid.uuid4().hex[:12].upper()}"

    d = Document(
        project_id=project.id,
        document_code=code,
        filename=filename,
        file_type=filepath.suffix.lower().lstrip("."),
        file_hash=digest,
        size_bytes=len(data),
        original_path=str(resolved_path),
        duplicate_of_id=duplicate.id if duplicate else None,
        parse_status="DUPLICATE" if duplicate else "UPLOADED",
        # Metadata with priority: explicit > inferred
        document_type=metadata.get("document_type") or inferred.get("document_type", "other"),
        entity_code=metadata.get("entity_code") or inferred.get("entity_code", ""),
        counterparty_code=metadata.get("counterparty_code") or inferred.get("counterparty_code", ""),
        business_category=metadata.get("business_category") or inferred.get("business_category", ""),
        tax_category=metadata.get("tax_category") or inferred.get("tax_category", ""),
        tax_vat_rate=metadata.get("tax_vat_rate", 0.0) or 0.0,
        tax_vat_input=metadata.get("tax_vat_input", 0.0) or 0.0,
        tax_vat_output=metadata.get("tax_vat_output", 0.0) or 0.0,
        tax_vat_paid=metadata.get("tax_vat_paid", 0.0) or 0.0,
        tax_income_rate=metadata.get("tax_income_rate", 0.0) or 0.0,
        tax_income_amount=metadata.get("tax_income_amount", 0.0) or 0.0,
        tax_income_paid=metadata.get("tax_income_paid", 0.0) or 0.0,
        tax_individual_rate=metadata.get("tax_individual_rate", 0.0) or 0.0,
        tax_individual_amount=metadata.get("tax_individual_amount", 0.0) or 0.0,
        tax_individual_paid=metadata.get("tax_individual_paid", 0.0) or 0.0,
        tax_surtax_urban=metadata.get("tax_surtax_urban", 0.0) or 0.0,
        tax_surtax_edu=metadata.get("tax_surtax_edu", 0.0) or 0.0,
        tax_surtax_local_edu=metadata.get("tax_surtax_local_edu", 0.0) or 0.0,
        tax_stamp_duty=metadata.get("tax_stamp_duty", 0.0) or 0.0,
        tax_land=metadata.get("tax_land", 0.0) or 0.0,
        tax_environmental=metadata.get("tax_environmental", 0.0) or 0.0,
        invoice_no=metadata.get("invoice_no", "") or "",
        invoice_code=metadata.get("invoice_code", "") or "",
        invoice_type=metadata.get("invoice_type", "") or "",
        invoice_date=metadata.get("invoice_date", "") or "",
        invoice_deductible=metadata.get("invoice_deductible", False) or False,
        tax_total=metadata.get("tax_total", 0.0) or 0.0,
        contract_no=metadata.get("contract_no", "") or "",
        period=metadata.get("period") or inferred.get("period", ""),
        metadata_confidence=float(inferred.get("confidence", 0.25)),
        metadata_source="filename",
    )
    db.add(d)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(d)

    job_id = None
    if auto_parse and not duplicate:
        try:
            job_id = enqueue_parse(db, d.id)
            logger.info(f"Queued parsing job {job_id} for document {d.id} from local path")
        except Exception as e:
            logger.error(f"Failed to queue parsing for {d.id}: {e}")

    return d, job_id
