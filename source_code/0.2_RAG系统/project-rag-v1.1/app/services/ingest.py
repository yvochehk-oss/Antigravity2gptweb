"""Document ingestion pipeline with improved error handling and cleanup.

Handles parsing, chunking, embedding, and cleanup of document content.
"""
import json
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import EMBEDDING_DIM, IS_POSTGRES
from ..logging_config import get_logger
from ..models import Chunk, Document, IngestJob
from .chunker import chunks_from_content_list, chunks_from_markdown, chunks_from_plain_text
from .embeddings import embed_many
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
            "business_category": doc.business_category,
            "tax_category": doc.tax_category
        }, preview)

        if doc.document_type == "other" and refined.get("document_type"):
            doc.document_type = refined["document_type"]
            doc.metadata_source = "content_rule"
            doc.metadata_confidence = max(doc.metadata_confidence, 0.65)

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
                heading_path=item.get("heading_path", "") or "",
                page_start=item.get("page_start"),
                page_end=item.get("page_end"),
                content_type=item.get("content_type", "text"),
                content=item["content"],
                token_estimate=int(item.get("token_estimate", 0)),
                embedding_json=json.dumps(vector),
                embedding=pgvec,
                search_text=(item.get("heading_path", "") + " " + item["content"]).lower()
            )
            chunks_to_add.append(chunk)

        db.add_all(chunks_to_add)

        # Step 6: Update document status
        doc.parse_status = "INDEXED"
        doc.parse_message = f"indexed {len(raw)} chunks"
        db.commit()
        db.refresh(doc)

        logger.info(
            f"Successfully indexed document {doc.document_code}: "
            f"{len(chunks_to_add)} chunks"
        )

        return doc

    except MinerUUnavailable as e:
        doc.parse_status = "WAITING_MINERU"
        doc.parse_message = str(e)
        db.commit()
        logger.warning(f"Document {doc.document_code} waiting for MinerU: {e}")
        return doc

    except Exception as e:
        doc.parse_status = "PARSE_FAILED"
        doc.parse_message = str(e)
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
