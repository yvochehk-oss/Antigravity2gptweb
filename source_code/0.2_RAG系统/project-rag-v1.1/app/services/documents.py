"""Document management service with improved error handling."""

import uuid
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import MAX_UPLOAD_SIZE
from ..domain.entities import is_canonical_entity_code
from ..logging_config import get_logger
from ..models import Chunk, Document, IngestJob, Project
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
    validate_safe_directory,
)
from .storage.write import (
    finalize_document_cleanup,
    restore_document_cleanup,
    stage_document_cleanup,
)

logger = get_logger(__name__)


def _lock_project_file_hash(
    db: Session,
    project_id: int,
    digest: str,
) -> None:
    """Serialize registration of one project/hash pair.

    ProjectRAG is PostgreSQL-only in production, so an advisory transaction lock
    gives us a concurrency-safe dedupe boundary without requiring a schema migration.
    In mock or non-PostgreSQL unit tests, this is gracefully skipped.
    """
    if not hasattr(db, "execute") or not hasattr(db, "get_bind"):
        return
    try:
        bind = db.get_bind()
        if bind and bind.dialect.name == "postgresql":
            lock_key = f"project-rag-document:{project_id}:{digest}"
            db.execute(
                text(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(:lock_key, 0)
                    )
                    """
                ),
                {"lock_key": lock_key},
            )
    except Exception:
        pass


def _find_existing_document(
    db: Session,
    project_id: int,
    digest: str,
) -> Document | None:
    """Return the canonical document for this project/hash."""
    return db.scalar(
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.file_hash == digest,
            Document.duplicate_of_id.is_(None),
        )
        .order_by(Document.id.asc())
        .limit(1)
    )


def register_bytes(
    db: Session, project: Project, filename: str, data: bytes, metadata: dict | None = None, auto_parse: bool = True
) -> tuple[Document, int | None]:
    """Register a document from bytes with idempotent deduplication.

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

    _lock_project_file_hash(db, project.id, digest)

    # Check for existing document in this project
    existing = _find_existing_document(db, project.id, digest)
    if existing is not None:
        logger.info(
            "Idempotent document skip: project=%s hash=%s existing_document=%s filename=%s",
            project.project_code,
            digest,
            existing.id,
            filename,
        )
        if hasattr(db, "rollback"):
            db.rollback()
        return existing, None

    # Infer metadata from the same canonical Entity master used by the
    # runtime UI/sync path.
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

    # Create document record - new imports are always canonical documents
    d = Document(
        project_id=project.id,
        document_code=code,
        filename=filename or code,
        file_type=Path(filename).suffix.lower().lstrip("."),
        file_hash=digest,
        size_bytes=len(data),
        original_path=str(path),
        duplicate_of_id=None,
        parse_status="UPLOADED",
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
        # 发票相关
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

    # 自动入队解析
    job_id = None
    if auto_parse:
        try:
            job_id = enqueue_parse(db, d.id)
            logger.info(f"Queued parsing job {job_id} for document {d.id}")
        except Exception as e:
            logger.error(f"Failed to queue parsing for {d.id}: {e}")

    return d, job_id


def scan_folder(
    db: Session,
    project: Project,
    folder: str | Path,
    recursive: bool = True,
    auto_parse: bool = True,
) -> list[dict]:
    """Scan a local directory and import all supported documents idempotently."""
    root = Path(folder).expanduser()
    if root.is_symlink():
        raise PathTraversalError("scanned folder cannot be a symlink")
    try:
        root = _validate_safe_path(root, operation="folder scan")
    except (StorageError, PathTraversalError):
        raise

    if not root.is_dir():
        raise StorageError(f"not a directory: {folder}")

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

            data = read_file_limited(path)
            digest = sha256_bytes(data)

            _lock_project_file_hash(db, project.id, digest)
            existing = _find_existing_document(db, project.id, digest)

            if existing is not None:
                if hasattr(db, "rollback"):
                    db.rollback()
                out.append(
                    {
                        "source": str(path),
                        "document_id": existing.id,
                        "document_code": existing.document_code,
                        "status": "SKIPPED_DUPLICATE",
                        "job_id": None,
                        "is_duplicate": True,
                        "skipped_duplicate": True,
                    }
                )
                continue

            d, jid = register_bytes(db, project, path.name, data, {}, auto_parse=auto_parse)
            out.append(
                {
                    "source": str(path),
                    "document_id": d.id,
                    "document_code": d.document_code,
                    "status": d.parse_status,
                    "job_id": jid,
                    "is_duplicate": False,
                    "skipped_duplicate": False,
                }
            )
        except (StorageError, PathTraversalError, OSError, ValueError) as e:
            if hasattr(db, "rollback"):
                db.rollback()
            out.append({"source": str(path), "error": str(e), "error_type": type(e).__name__})
            logger.error(f"Failed to import {path}: {e}")

    imported = sum(1 for r in out if "error" not in r and not r.get("skipped_duplicate"))
    skipped = sum(1 for r in out if r.get("skipped_duplicate"))
    failed = sum(1 for r in out if "error" in r)

    logger.info(
        "Folder scan complete: scanned=%s imported=%s skipped_duplicates=%s failed=%s",
        len(out),
        imported,
        skipped,
        failed,
    )

    return out


def register_local_path(
    db: Session, project: Project, filepath: Path, metadata: dict | None = None, auto_parse: bool = True
) -> tuple[Document, int | None]:
    """Register a document directly from a trusted local path idempotently."""
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

    _lock_project_file_hash(db, project.id, digest)

    # Check for duplicate
    existing = _find_existing_document(db, project.id, digest)
    if existing is not None:
        logger.info(
            "Idempotent local-path skip: project=%s hash=%s existing_document=%s source=%s",
            project.project_code,
            digest,
            existing.id,
            resolved_path,
        )
        if hasattr(db, "rollback"):
            db.rollback()
        return existing, None

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
        duplicate_of_id=None,
        parse_status="UPLOADED",
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
    if auto_parse:
        try:
            job_id = enqueue_parse(db, d.id)
            logger.info(f"Queued parsing job {job_id} for document {d.id} from local path")
        except Exception as e:
            logger.error(f"Failed to queue parsing for {d.id}: {e}")

    return d, job_id


def _managed_duplicate_storage_path(
    document: Document,
    raw_path: str | None,
) -> str | None:
    """Return only storage that is clearly owned by this Document row.

    save_original()/migration-012 paths contain document_code as a directory.
    A local source path imported via register_local_path normally does not.
    That distinction prevents self-heal from deleting the user's source file.
    """
    if not raw_path:
        return None

    try:
        resolved = Path(raw_path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None

    if document.document_code not in resolved.parts:
        return None

    return str(resolved)


def purge_redundant_duplicate_documents(
    db: Session,
    project_id: int,
) -> dict[str, int]:
    """Remove historical synthetic DUPLICATE rows safely and idempotently."""
    duplicates = db.scalars(
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.parse_status == "DUPLICATE",
            Document.duplicate_of_id.is_not(None),
        )
        .order_by(Document.id.asc())
    ).all()

    removed = 0
    protected = 0
    storage_cleaned = 0
    storage_cleanup_pending = 0

    for document in duplicates:
        chunk_count = db.scalar(
            select(func.count(Chunk.id)).where(Chunk.document_id == document.id)
        ) or 0

        job_count = db.scalar(
            select(func.count(IngestJob.id)).where(IngestJob.document_id == document.id)
        ) or 0

        # A historical DUPLICATE should never own parsed/indexed content.
        # If it does, do not guess which row is canonical.
        if chunk_count or job_count:
            protected += 1
            continue

        original_path = _managed_duplicate_storage_path(
            document,
            document.original_path,
        )
        parsed_dir = _managed_duplicate_storage_path(
            document,
            getattr(document, "parsed_dir", None),
        )

        staged = None

        try:
            staged = stage_document_cleanup(
                original_path,
                parsed_dir,
            )

            if hasattr(db, "begin_nested"):
                with db.begin_nested():
                    db.delete(document)
                    if hasattr(db, "flush"):
                        db.flush()
            else:
                db.delete(document)
                if hasattr(db, "flush"):
                    db.flush()

            db.commit()
            removed += 1

        except IntegrityError:
            db.rollback()

            if staged is not None:
                restore_document_cleanup(staged)

            protected += 1

            logger.warning(
                "Historical duplicate retained because it is still referenced: document_id=%s",
                document.id,
            )
            continue

        except Exception:
            db.rollback()

            if staged is not None:
                restore_document_cleanup(staged)

            raise

        if staged is not None:
            try:
                finalize_document_cleanup(staged)
                storage_cleaned += 1
            except Exception:
                storage_cleanup_pending += 1
                logger.exception(
                    "Duplicate DB row removed but storage finalization is pending: document_id=%s",
                    document.id,
                )

    return {
        "removed": removed,
        "protected": protected,
        "storage_cleaned": storage_cleaned,
        "storage_cleanup_pending": storage_cleanup_pending,
    }


def repair_filename_classifications(
    db: Session,
    project_id: int,
) -> dict[str, int]:
    """Repair only deterministic strong-evidence filename mistakes.

    User-edited metadata is never touched.
    """
    canonical_cache = load_canonical_entity_cache()

    documents = db.scalars(
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.duplicate_of_id.is_(None),
            Document.metadata_source == "filename",
        )
        .order_by(Document.id.asc())
    ).all()

    checked = 0
    changed = 0

    for document in documents:
        inferred = infer_from_filename(
            document.filename,
            canonical_cache=canonical_cache,
        )

        source = str(inferred.get("classification_source") or "")

        # Do not mass-reclassify ordinary fuzzy rules.
        # Self-heal is limited to deterministic high-confidence evidence.
        if not (source.startswith("prefix:") or source == "strong_evidence"):
            continue

        checked += 1

        new_type = str(inferred.get("document_type") or "other")
        new_category = str(inferred.get("business_category") or "")
        new_tax_category = str(inferred.get("tax_category") or "")

        before = (
            document.document_type,
            document.business_category,
            document.tax_category,
        )

        after = (
            new_type,
            new_category,
            new_tax_category,
        )

        if before == after:
            continue

        document.document_type = new_type
        document.business_category = new_category
        document.tax_category = new_tax_category
        document.metadata_confidence = max(
            float(document.metadata_confidence or 0),
            float(inferred.get("confidence") or 0),
        )

        changed += 1

    if changed:
        db.commit()

    return {
        "checked": checked,
        "reclassified": changed,
    }
