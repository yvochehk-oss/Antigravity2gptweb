from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID

from .repository import IDPRepository


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def get_latest_result_by_sha(repository: IDPRepository, sha256: str) -> Optional[Dict[str, Any]]:
    """Return the latest persisted extraction in the same shape as the process API.

    This is used before OCR/LLM work so identical files can be returned directly
    without creating another extraction or another pending review.
    """
    if not repository.enabled:
        return None

    with repository.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                d.id AS document_id,
                d.sha256,
                d.filename,
                d.file_type,
                d.file_path,
                d.document_type,
                d.page_count,
                d.parser,
                d.ocr_confidence,
                d.status AS document_status,
                e.id AS extraction_id,
                e.extracted_data,
                e.validation_result,
                e.status AS extraction_status,
                r.id AS review_id,
                r.status AS review_status
            FROM documents d
            LEFT JOIN LATERAL (
                SELECT id, extracted_data, validation_result, status
                FROM document_extractions
                WHERE document_id = d.id
                ORDER BY created_at DESC
                LIMIT 1
            ) e ON TRUE
            LEFT JOIN LATERAL (
                SELECT id, status
                FROM document_reviews
                WHERE document_id = d.id
                  AND (e.id IS NULL OR extraction_id = e.id)
                ORDER BY created_at DESC
                LIMIT 1
            ) r ON TRUE
            WHERE d.sha256 = %s
            LIMIT 1
            """,
            (sha256,),
        )
        row = cur.fetchone()
        if not row:
            return None

    stored = dict(row)
    validation_bundle = dict(stored.get("validation_result") or {})
    result: Dict[str, Any] = {
        "sha256": stored["sha256"],
        "document_type": stored.get("document_type"),
        "parser": stored.get("parser"),
        "page_count": stored.get("page_count") or 0,
        "ocr_confidence": stored.get("ocr_confidence"),
        "data": dict(stored.get("extracted_data") or {}),
        "validation": dict(validation_bundle.get("validation") or {}),
        "audit": dict(validation_bundle.get("audit") or {}),
        "status": stored.get("document_status") or stored.get("extraction_status"),
        "persistence": {
            "enabled": True,
            "stored": True,
            "duplicate": True,
            "document_id": str(stored["document_id"]),
            "extraction_id": str(stored["extraction_id"]) if stored.get("extraction_id") else None,
            "review_id": str(stored["review_id"]) if stored.get("review_id") else None,
            "status": stored.get("document_status") or stored.get("extraction_status"),
            "committed": stored.get("document_status") == "committed",
        },
    }
    return _jsonable(result)


def get_document_for_reprocess(repository: IDPRepository, document_id: str | UUID) -> Optional[Dict[str, Any]]:
    if not repository.enabled:
        return None
    with repository.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, sha256, filename, file_type, file_path, document_type, status
            FROM documents
            WHERE id = %s
            LIMIT 1
            """,
            (document_id,),
        )
        row = cur.fetchone()
        return _jsonable(dict(row)) if row else None


def supersede_pending_reviews(
    repository: IDPRepository,
    document_id: str | UUID,
    *,
    keep_review_id: str | UUID | None = None,
) -> int:
    """Close stale pending reviews after a forced reprocess.

    The latest pending review can be preserved with keep_review_id. When a new
    extraction commits automatically, keep_review_id is omitted and all stale
    pending reviews are superseded.
    """
    if not repository.enabled:
        return 0

    with repository.connection() as conn, conn.cursor() as cur:
        if keep_review_id is None:
            cur.execute(
                """
                UPDATE document_reviews
                SET status='superseded', reviewed_at=NOW()
                WHERE document_id=%s AND status='pending'
                """,
                (document_id,),
            )
        else:
            cur.execute(
                """
                UPDATE document_reviews
                SET status='superseded', reviewed_at=NOW()
                WHERE document_id=%s AND status='pending' AND id<>%s
                """,
                (document_id, keep_review_id),
            )
        updated = cur.rowcount
        conn.commit()
        return int(updated or 0)


def validate_stored_path(document: Dict[str, Any], storage_root: str | Path) -> Path:
    path_value = str(document.get("file_path") or "").strip()
    if not path_value:
        raise FileNotFoundError("stored_original_not_available")
    root = Path(storage_root).expanduser().resolve()
    path = Path(path_value).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FileNotFoundError("stored_original_outside_storage_root") from exc
    if not path.is_file():
        raise FileNotFoundError(f"stored_original_not_found: {path}")
    return path
