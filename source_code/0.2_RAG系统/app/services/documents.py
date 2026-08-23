"""Document management service with improved error handling."""
from pathlib import Path
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import Project, Document
from .storage import sha256_bytes, save_original, SAFE_EXTS, StorageError, PathTraversalError, _validate_safe_path
from .metadata import infer_from_filename
from .jobs import enqueue_parse
from ..logging_config import get_logger

logger = get_logger(__name__)


def register_bytes(
    db: Session,
    project: Project,
    filename: str,
    data: bytes,
    metadata: dict | None = None,
    auto_parse: bool = True
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
    digest = sha256_bytes(data)

    # Check for duplicate
    duplicate = db.scalar(
        select(Document).where(
            Document.project_id == project.id,
            Document.file_hash == digest,
            Document.duplicate_of_id.is_(None)
        )
    )

    # Infer metadata from filename
    inferred = infer_from_filename(filename)

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
        metadata_source="filename"
    )

    db.add(d)
    db.commit()
    db.refresh(d)

    logger.info(
        f"Registered document {d.document_code} for project {project.project_code}"
        f"{' (duplicate)' if duplicate else ''}"
    )

    # Queue for parsing
    jid = None
    if auto_parse and not duplicate:
        job = enqueue_parse(db, d.id)
        jid = job.id
        db.refresh(d)

    return d, jid


def scan_folder(
    db: Session,
    project: Project,
    folder: str,
    recursive: bool = True,
    auto_parse: bool = True
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
    root = Path(folder).expanduser().resolve()

    if not root.exists() or not root.is_dir():
        raise ValueError(f"Folder does not exist: {folder}")

    # Security: validate folder is within allowed directories
    try:
        _validate_safe_path(root)
    except (StorageError, PathTraversalError) as e:
        raise ValueError(f"Folder not accessible: {e}")

    # Security: validate folder is accessible
    try:
        list(root.iterdir())
    except PermissionError:
        raise ValueError(f"Permission denied: {folder}")

    globber = root.rglob("*") if recursive else root.glob("*")
    out = []

    for path in globber:
        if not path.is_file():
            continue

        if path.suffix.lower() not in SAFE_EXTS:
            continue

        try:
            d, jid = register_bytes(
                db, project, path.name, path.read_bytes(),
                {}, auto_parse=auto_parse
            )
            out.append({
                "source": str(path),
                "document_id": d.id,
                "document_code": d.document_code,
                "status": d.parse_status,
                "job_id": jid,
                "is_duplicate": d.duplicate_of_id is not None
            })
        except (StorageError, PathTraversalError, OSError) as e:
            out.append({
                "source": str(path),
                "error": str(e),
                "error_type": type(e).__name__
            })
            logger.error(f"Failed to import {path}: {e}")

    logger.info(
        f"Folder scan complete: {len(out)} files, "
        f"{sum(1 for r in out if 'error' not in r)} imported"
    )

    return out
