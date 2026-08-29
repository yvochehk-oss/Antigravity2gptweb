"""Document ingestion pipeline with improved error handling and cleanup.

Handles parsing, chunking, embedding, and cleanup of document content.
"""
import json
import re
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import EMBEDDING_DIM, IS_POSTGRES
from ..logging_config import get_logger
from ..domain.entities import is_canonical_entity_code
from ..models import Chunk, Document, ExternalParty, IngestJob, Project
from .chunker import chunks_from_content_list, chunks_from_markdown, chunks_from_plain_text
from .embeddings import embed_many
from .extractor import extract_invoice_fields_from_text
from .metadata import refine_from_content
from .mineru_adapter import (
    MinerUUnavailable,
    assess_parse_quality,
    detect_encrypted_pdf,
    parse_with_mineru,
)
from .storage.write import (
    finalize_document_cleanup,
    restore_document_cleanup,
    stage_document_cleanup,
)

logger = get_logger(__name__)


_INVOICE_DOCUMENT_TYPES = frozenset({"invoice", "receipt", "tax_invoice"})
_INVOICE_MARKERS = ("发票号码", "发票代码", "价税合计", "增值税专用发票", "增值税普通发票")


def _invoice_candidate_text(raw: list[dict]) -> str:
    """Join parsed text for document-level invoice extraction.

    Invoice fields are frequently split across OCR pages/chunks.  Extraction
    at chunk level is still used by the Tax endpoint, but document metadata
    needs one bounded source text so an invoice number on page one and totals
    on page two are validated together.
    """
    parts = [str(item.get("content") or "") for item in raw if isinstance(item, dict)]
    return "\n".join(parts)[:120_000]


def _looks_like_invoice(doc: Document, text: str) -> bool:
    if str(getattr(doc, "document_type", "") or "").strip().lower() in _INVOICE_DOCUMENT_TYPES:
        return True
    marker_count = sum(1 for marker in _INVOICE_MARKERS if marker in text)
    return marker_count >= 2 and bool(re.search(r"(?:发票号码|发票代码)\s*[:：]", text))


def _has_document_value(value, *, numeric: bool = False) -> bool:
    """Distinguish an explicit value from the ORM's empty defaults."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if numeric:
        return value != 0
    return bool(value)


def _persist_invoice_metadata(doc: Document, raw: list[dict]) -> dict | None:
    """Persist deterministic invoice metadata without overwriting user input.

    ``Document`` predates the full invoice contract and has no net/gross
    columns.  Those values and arithmetic evidence therefore remain in the
    structured ``parse_message`` payload, while the existing invoice/tax
    columns are populated only when they are still empty.  No filename or
    document code is ever used as an invoice number.
    """
    text = _invoice_candidate_text(raw)
    if not text or not _looks_like_invoice(doc, text):
        return None

    fields = extract_invoice_fields_from_text(text)
    if not any(fields.get(key) not in (None, "") for key in ("invoice_no", "invoice_code", "invoice_date")):
        return None

    text_updates = {
        "invoice_no": fields.get("invoice_no"),
        "invoice_code": fields.get("invoice_code"),
        "invoice_date": fields.get("invoice_date"),
        "invoice_type": fields.get("invoice_type"),
        "period": fields.get("period"),
        "document_date": fields.get("invoice_date"),
    }
    for name, value in text_updates.items():
        if value not in (None, "") and not _has_document_value(getattr(doc, name, None)):
            setattr(doc, name, str(value))

    rate = fields.get("vat_rate")
    if rate not in (None, "") and not _has_document_value(getattr(doc, "tax_vat_rate", 0), numeric=True):
        # The ORM/API contract stores this field as a percentage (13.0),
        # whereas the extraction contract uses a decimal rate (0.13).
        doc.tax_vat_rate = float(rate) * 100

    vat = fields.get("vat_amount")
    direction = str(fields.get("direction") or "").strip().lower()
    if vat not in (None, ""):
        vat_value = float(vat)
        if direction == "in" and not _has_document_value(getattr(doc, "tax_vat_input", 0), numeric=True):
            doc.tax_vat_input = vat_value
        elif direction == "out" and not _has_document_value(getattr(doc, "tax_vat_output", 0), numeric=True):
            doc.tax_vat_output = vat_value
        if not _has_document_value(getattr(doc, "tax_total", 0), numeric=True):
            doc.tax_total = vat_value

    return {
        "schema_version": "invoice_metadata_v1",
        "status": fields.get("validation_status", "UNVALIDATED"),
        "fields": {
            key: fields.get(key)
            for key in (
                "invoice_no", "invoice_code", "invoice_date", "period", "direction",
                "invoice_type", "seller_name", "seller_tax_id", "buyer_name",
                "buyer_tax_id", "total_amount", "net_amount", "vat_amount", "vat_rate",
                "deductible", "category",
            )
            if fields.get(key) not in (None, "")
        },
        "validation_errors": list(fields.get("validation_errors") or []),
        "extraction_warnings": list(fields.get("extraction_warnings") or []),
        "arithmetic_validation": dict(fields.get("arithmetic_validation") or {}),
        "evidence": dict(fields.get("evidence") or {}),
        "source": fields.get("source", "deterministic_ocr_rules"),
    }


def cleanup_document_files(doc: Document) -> None:
    """Clean up document's original and parsed files.

    Args:
        doc: Document to clean up
    """
    cleanup_paths(doc.original_path, doc.parsed_dir)


def cleanup_paths(original_path: str, parsed_dir: str) -> None:
    """Clean up document files by path with strict failure semantics.

    This helper is used outside the HTTP deletion transaction as well.  It
    uses the same reversible staging protocol and only reports success after
    every staged object has been finalized.  A cleanup failure is propagated;
    callers must not infer that a file was deleted from a best-effort boolean.
    """
    transaction = stage_document_cleanup(original_path, parsed_dir)
    try:
        finalize_document_cleanup(transaction)
    except Exception:
        # If finalization failed before any staged object was removed, restore
        # the files.  If finalization partially removed them, the restore call
        # raises and the durable manifest remains for operator recovery.
        try:
            restore_document_cleanup(transaction)
        except Exception as restore_exc:
            logger.error("Document cleanup recovery failed: %s", restore_exc)
        raise


def delete_document_chunks(db: Session, document_id: int) -> int:
    """Delete all chunks for a document.

    Args:
        db: Database session
        document_id: Document ID

    Returns:
        Number of chunks deleted
    """
    result = db.execute(delete(Chunk).where(Chunk.document_id == document_id))
    count = result.rowcount
    logger.debug(f"Deleted {count} chunks for document {document_id}")
    return count


def _safe_load_markdown(markdown_path: str) -> str:
    """Best-effort markdown loader used by parse-quality assessment."""
    if not markdown_path:
        return ""
    try:
        p = Path(markdown_path)
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return ""


def _update_ingest_job_quality(
    db: Session,
    document_id: int,
    score: float | None,
    flags: list[str],
    is_encrypted: bool = False,
) -> None:
    """Persist parse-quality info onto the most recent IngestJob.

    Best-effort: any failure is logged and swallowed so it never breaks the
    surrounding ingestion transaction. The function performs its own
    commit so IngestJob updates land even when the outer call eventually
    fails.

    Args:
        db: Active SQLAlchemy session.
        document_id: Document whose job should be updated.
        score: Quality score 0-100, or None to leave unchanged.
        flags: List of flag strings to JSON-encode.
        is_encrypted: True iff encryption was detected at upload time.
    """
    try:
        job = db.scalar(
            select(IngestJob)
            .where(IngestJob.document_id == document_id)
            .order_by(IngestJob.id.desc())
            .limit(1)
        )
        if not job:
            return
        if score is not None:
            job.parse_quality_score = float(score)
        job.parse_quality_flags_json = json.dumps(list(flags or []))
        if is_encrypted:
            job.is_encrypted = True
        db.commit()
    except Exception as e:
        logger.warning(
            f"Failed to update IngestJob quality for document {document_id}: {e}"
        )
        try:
            db.rollback()
        except Exception:
            pass


def _auto_register_external_party(
    db: Session,
    counterparty_code: str | None,
    counterparty_name: str | None = None,
    tax_id: str | None = None,
    kind: str = "partner",
) -> None:
    """Automatically discover and register system-external parties from ingested documents."""
    raw_code = (counterparty_code or "").strip().upper()
    if not raw_code or is_canonical_entity_code(raw_code):
        return

    from ..domain.entities import get_external_preset, map_to_standard_external_code

    preset = get_external_preset(raw_code)
    code = preset["code"] if preset else map_to_standard_external_code(raw_code)
    name = counterparty_name if (counterparty_name and counterparty_name not in (raw_code, code, "")) else (preset["name"] if preset else code)
    short_name = preset.get("short_name") if preset else name
    tax_id = tax_id or (preset.get("tax_id") if preset else None)
    
    if preset:
        kind = preset.get("kind") or kind
    else:
        if code.startswith("EA"):
            kind = "construction"
        elif code.startswith("EB"):
            kind = "trade"
        elif code.startswith("EC"):
            kind = "labor"
        elif code.startswith("ED"):
            kind = "equipment"
        elif code.startswith("E0"):
            kind = "owner"

    try:
        existing = db.scalar(select(ExternalParty).where(ExternalParty.code == code))
        if existing:
            if name and existing.name in (code, raw_code, ""):
                existing.name = name
            if short_name and (not existing.short_name or existing.short_name in (code, raw_code)):
                existing.short_name = short_name
            if tax_id and not existing.tax_id:
                existing.tax_id = tax_id
            if kind and (not existing.kind or existing.kind == "partner"):
                existing.kind = kind
            return

        new_party = ExternalParty(
            code=code,
            name=name,
            short_name=short_name,
            kind=kind,
            tax_id=tax_id or None,
            active=True,
        )
        db.add(new_party)
        db.flush()
        logger.info(f"Auto-registered new external party: {code} ({name}) - {kind}")
    except Exception as e:
        logger.warning(f"Auto-register external party failed for {code}: {e}")


def parse_and_index(db: Session, doc: Document) -> Document:
    """Parse document and index its chunks with embeddings.

    This is the main ingestion pipeline. It:
    1. Parses the document using MinerU or direct reading
    2. Chunks the content with page/heading awareness
    3. Generates embeddings for each chunk
    4. Stores chunks in the database

    Args:
        db: Database session
        doc: Document to parse and index

    Returns:
        Updated document with parse_status
    """
    ext = Path(doc.original_path).suffix.lower()

    logger.info(f"Starting parse for document {doc.document_code} ({ext})")

    # Pre-flight: reject encrypted PDFs before invoking MinerU.
    if ext == ".pdf" and detect_encrypted_pdf(doc.original_path):
        doc.parse_status = "PASSWORD_REQUIRED"
        doc.parse_message = "PDF文件已加密，请提供密码后重新上传"
        db.commit()
        db.refresh(doc)
        _update_ingest_job_quality(
            db,
            doc.id,
            score=0.0,
            flags=["ENCRYPTED_PDF"],
            is_encrypted=True,
        )
        logger.warning(
            f"Document {doc.document_code} is encrypted; skipping MinerU"
        )
        return doc

    try:
        # Step 1: Parse document
        if ext in (".md", ".txt", ".html"):
            doc.parsed_dir = str(Path(doc.original_path).parent)
            doc.markdown_path = doc.original_path if ext == ".md" else ""
            doc.content_list_path = ""

            if ext == ".md":
                raw = chunks_from_markdown(doc.original_path)
            else:
                raw = chunks_from_plain_text(doc.original_path)

            doc.parse_status = "PARSED"
            logger.debug(f"Parsed {doc.filename} directly, got {len(raw)} raw chunks")

        else:
            # Use MinerU for PDF, DOCX, etc.
            result = parse_with_mineru(doc.document_code, doc.original_path)
            doc.parsed_dir = result["output_dir"]
            doc.markdown_path = result["markdown_path"]
            doc.content_list_path = result["content_list_path"]
            doc.parse_status = "PARSED"

            # V0.3 parse-quality gate (early, before chunking work).
            try:
                md_text = _safe_load_markdown(doc.markdown_path)
                quality = assess_parse_quality(result or {}, md_text)
                _update_ingest_job_quality(
                    db,
                    doc.id,
                    score=quality.get("score", 0.0),
                    flags=quality.get("flags", []),
                    is_encrypted=False,
                )
            except Exception as qe:
                logger.warning(
                    f"Parse quality assessment failed for {doc.document_code}: {qe}"
                )

            # Convert MinerU output to chunks
            if doc.content_list_path:
                raw = chunks_from_content_list(doc.content_list_path)
            elif doc.markdown_path:
                raw = chunks_from_markdown(doc.markdown_path)
            else:
                raise RuntimeError("MinerU produced no usable output")

            logger.debug(f"MinerU parsed {doc.filename}, got {len(raw)} raw chunks")

        # Validate we got content
        if not raw:
            raise RuntimeError("Parser produced no indexable chunks")

        # Step 2: Refine metadata from content preview
        preview = "\n".join(x.get("content", "") for x in raw[:5])
        refined = refine_from_content({
            "document_type": doc.document_type,
            "entity_code": doc.entity_code,
            "counterparty_code": doc.counterparty_code,
            "business_category": doc.business_category,
            "tax_category": doc.tax_category
        }, preview)

        if doc.document_type == "other" and refined.get("document_type"):
            doc.document_type = refined["document_type"]
            doc.metadata_source = "content_rule"
            doc.metadata_confidence = max(doc.metadata_confidence, 0.65)

        if not doc.entity_code and refined.get("entity_code"):
            doc.entity_code = refined["entity_code"]

        if not doc.counterparty_code and refined.get("counterparty_code"):
            doc.counterparty_code = refined["counterparty_code"]

        if doc.counterparty_code:
            _auto_register_external_party(
                db,
                doc.counterparty_code,
                refined.get("counterparty_name"),
                refined.get("counterparty_tax_id"),
            )

        if not doc.entity_code:
            p = db.get(Project, doc.project_id)
            if p and p.entity_code:
                doc.entity_code = p.entity_code

        if not doc.business_category and refined.get("business_category"):
            doc.business_category = refined["business_category"]

        if not doc.tax_category and refined.get("tax_category"):
            doc.tax_category = refined["tax_category"]

        # Step 3: Delete existing chunks (for re-indexing)
        deleted_count = delete_document_chunks(db, doc.id)
        logger.debug(f"Deleted {deleted_count} existing chunks")

        # Step 4: Generate chunks and embeddings
        texts = [
            (item.get("heading_path", "") + "\n" + item["content"])[:12000]
            for item in raw
        ]

        vectors = embed_many(texts)

        # Step 5: Store chunks
        chunks_to_add = []
        for idx, (item, vector) in enumerate(zip(raw, vectors)):
            # Validate vector dimension for PostgreSQL
            pgvec = vector if IS_POSTGRES and len(vector) == EMBEDDING_DIM else None

            chunk = Chunk(
                document_id=doc.id,
                project_id=doc.project_id,
                chunk_index=idx,
                heading_path=(item.get("heading_path", "") or "").replace("\x00", ""),
                page_start=item.get("page_start"),
                page_end=item.get("page_end"),
                content_type=item.get("content_type", "text"),
                content=item["content"].replace("\x00", ""),
                token_estimate=int(item.get("token_estimate", 0)),
                embedding_json=json.dumps(vector),
                embedding=pgvec,
                search_text=((item.get("heading_path", "") or "") + " " + item["content"]).replace("\x00", "").lower()
            )
            chunks_to_add.append(chunk)

        db.add_all(chunks_to_add)

        # Step 6: Persist document-level deterministic invoice metadata before
        # committing the chunks.  This is intentionally independent from the
        # optional LLM: the source OCR remains the authority for invoice
        # number/code/date and arithmetic evidence.
        invoice_metadata = _persist_invoice_metadata(doc, raw)
        doc.parse_status = "INDEXED"
        if invoice_metadata is None:
            doc.parse_message = f"indexed {len(raw)} chunks"
        else:
            doc.parse_message = json.dumps(
                {
                    "message": f"indexed {len(raw)} chunks",
                    "invoice_validation": invoice_metadata,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        db.commit()
        db.refresh(doc)

        logger.info(
            f"Successfully indexed document {doc.document_code}: "
            f"{len(chunks_to_add)} chunks"
        )

        return doc

    except MinerUUnavailable as e:
        db.rollback()
        doc.parse_status = "WAITING_MINERU"
        doc.parse_message = str(e).replace("\\x00", "")
        db.commit()
        logger.warning(f"Document {doc.document_code} waiting for MinerU: {e}")
        return doc

    except Exception as e:
        db.rollback()
        doc.parse_status = "PARSE_FAILED"
        doc.parse_message = str(e).replace("\\x00", "")
        db.commit()
        logger.error(f"Failed to parse document {doc.document_code}: {e}")

        # Partial parse-quality assessment: only meaningful when MinerU
        # already produced some output before the failure.
        try:
            if doc.content_list_path or doc.markdown_path:
                md_text = _safe_load_markdown(doc.markdown_path)
                quality = assess_parse_quality(
                    {
                        "markdown_path": doc.markdown_path,
                        "content_list_path": doc.content_list_path,
                    },
                    md_text,
                )
                flags = list(quality.get("flags", [])) + ["PARSE_FAILED"]
                _update_ingest_job_quality(
                    db,
                    doc.id,
                    score=quality.get("score", 0.0),
                    flags=flags,
                    is_encrypted=False,
                )
        except Exception as qe:
            logger.debug(f"Partial parse quality assessment failed: {qe}")

        return doc


def reindex_document(db: Session, document_id: int) -> Document:
    """Re-index a document, cleaning up old chunks first.

    Args:
        db: Database session
        document_id: Document ID

    Returns:
        Updated document

    Raises:
        ValueError: If document not found
    """
    doc = db.get(Document, document_id)
    if not doc:
        raise ValueError(f"Document {document_id} not found")

    # Delete old chunks
    delete_document_chunks(db, document_id)

    # Re-parse and index
    return parse_and_index(db, doc)
