from __future__ import annotations

import os
import logging
import secrets
import shutil
import tempfile
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, Literal, Optional

import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from .extractors import HybridExtractor
from .granite_client import GraniteAuditClient
from .ling_client import LingClient
from .ocr import PaddleOCRAdapter
from .parsers import DocumentParser
from .persistence_service import (
    get_document_for_reprocess,
    get_latest_result_by_sha,
    supersede_pending_reviews,
    validate_stored_path,
)
from .pipeline import IDPPipeline
from .repository import DuplicateBusinessRecordError, IDPRepository, ReviewDataValidationError


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="成都建工 IDP V3.0", version="3.0.0")
logger = logging.getLogger("cdjg.idp")


def _enabled(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() not in {"0", "false", "no", "off"}


def _positive_int(name: str, default: str) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


class ReviewCompleteRequest(BaseModel):
    action: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=200)
    review_data: Optional[Dict[str, Any]] = None


ling_enabled = _enabled("LING_ENABLED", "1")
ocr_enabled = _enabled("OCR_ENABLED", "1")
granite_enabled = _enabled("GRANITE_ENABLED", "0")
store_originals = _enabled("STORE_ORIGINALS", "1")
storage_dir = Path(os.getenv("IDP_STORAGE_DIR", "./storage/originals"))
if not storage_dir.is_absolute():
    storage_dir = BASE_DIR / storage_dir
storage_dir = storage_dir.resolve()
max_upload_bytes = _positive_int("IDP_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
idp_api_key = os.getenv("IDP_API_KEY", "").strip()
localhost_only = _enabled("IDP_LOCALHOST_ONLY", "1")

ling_client = LingClient() if ling_enabled else None
ocr_adapter = PaddleOCRAdapter() if ocr_enabled else None
granite_client = GraniteAuditClient() if granite_enabled else None
repository = IDPRepository()

pipeline = IDPPipeline(
    DocumentParser(ocr_parser=ocr_adapter),
    HybridExtractor(ling_client),
    auditor=granite_client,
)


def require_api_access(request: Request) -> None:
    """Require a configured API key, or permit only in-process loopback use."""
    if idp_api_key:
        supplied = request.headers.get("X-IDP-API-Key", "")
        if not secrets.compare_digest(supplied, idp_api_key):
            raise HTTPException(status_code=401, detail="invalid_idp_api_key")
        return
    if localhost_only and request.client and _is_loopback(request.client.host):
        return
    raise HTTPException(status_code=503, detail="IDP_API_KEY is required for non-local access")


def _request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "").strip()
    if supplied and len(supplied) <= 128:
        return supplied
    return uuid.uuid4().hex


def _log_database_error(request: Optional[Request], exc: BaseException, operation: str) -> str:
    request_id = _request_id(request) if request is not None else uuid.uuid4().hex
    logger.exception("database error request_id=%s operation=%s", request_id, operation)
    return request_id


def _database_http_exception(request: Request, exc: BaseException, operation: str) -> HTTPException:
    request_id = _log_database_error(request, exc, operation)
    return HTTPException(
        status_code=503,
        detail={"code": "database_unavailable", "request_id": request_id},
    )


def _log_runtime_error(request: Optional[Request], exc: BaseException, operation: str) -> str:
    request_id = _request_id(request) if request is not None else uuid.uuid4().hex
    logger.exception("runtime error request_id=%s operation=%s", request_id, operation)
    return request_id


def _service_http_exception(request: Request, exc: BaseException, operation: str) -> HTTPException:
    request_id = _log_runtime_error(request, exc, operation)
    return HTTPException(
        status_code=503,
        detail={"code": "service_unavailable", "request_id": request_id},
    )


def _require_database() -> None:
    if not repository.enabled:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")


def _store_original(tmp_path: Path, sha256: str, suffix: str) -> Path:
    if not store_originals:
        return tmp_path
    storage_dir.mkdir(parents=True, exist_ok=True)
    destination = storage_dir / f"{sha256}{suffix}"
    if not destination.exists():
        shutil.copy2(tmp_path, destination)
    return destination


def _persist_result(
    *,
    filename: str,
    file_type: str,
    file_path: str,
    raw_text: str,
    result: Dict[str, Any],
    request: Optional[Request] = None,
) -> Dict[str, Any]:
    persistence = repository.persist_result(
        filename=filename,
        file_type=file_type,
        file_path=file_path,
        raw_text=raw_text,
        result=result,
        model_name=ling_client.model if ling_client else "rules-only",
    )
    if persistence.get("stored") and persistence.get("document_id"):
        keep_review_id = persistence.get("review_id")
        try:
            persistence["superseded_reviews"] = supersede_pending_reviews(
                repository,
                persistence["document_id"],
                keep_review_id=keep_review_id,
            )
        except psycopg.Error as exc:
            # The primary persistence transaction has already committed. A
            # cleanup failure must not be reported as if the document itself
            # was lost; expose it separately for operators to retry/inspect.
            request_id = _log_database_error(request, exc, "supersede_reviews")
            persistence["supersede_error"] = "database_unavailable"
            persistence["supersede_request_id"] = request_id
    return persistence


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "cdjg-idp",
        "version": "3.0.0",
        "semantic_model": ling_client.model if ling_client else None,
        "semantic_enabled": ling_enabled,
        "ocr_enabled": ocr_enabled,
        "audit_enabled": granite_enabled,
        "audit_model": granite_client.model if granite_client else None,
        "database_configured": repository.enabled,
        "database_ok": repository.health() if repository.enabled else False,
        "store_originals": store_originals,
        "storage_dir": str(storage_dir),
        "api_auth_mode": "api_key" if idp_api_key else "localhost_only",
        "max_upload_bytes": max_upload_bytes,
    }


@app.post("/api/v3/documents/process", dependencies=[Depends(require_api_access)])
async def process_document(
    request: Request,
    file: UploadFile = File(...),
    force: bool = Query(default=False, description="Reprocess an identical SHA instead of returning its latest result"),
) -> dict:
    filename = file.filename or "upload.bin"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        raise HTTPException(status_code=415, detail="unsupported file type")

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = Path(tmp.name)
            total = 0
            while True:
                chunk = await file.read(min(1024 * 1024, max_upload_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_upload_bytes:
                    raise HTTPException(status_code=413, detail="file_too_large")
                tmp.write(chunk)
    except (RuntimeError, OSError) as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise _service_http_exception(request, exc, "upload") from exc
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise

    try:
        sha256 = pipeline.sha256(tmp_path)
        lock_factory = getattr(repository, "processing_lock", None)
        lock = lock_factory(sha256) if callable(lock_factory) else nullcontext()
        with lock:
            if repository.enabled and not force:
                try:
                    existing = get_latest_result_by_sha(repository, sha256)
                except psycopg.Error as exc:
                    _log_database_error(request, exc, "dedupe_lookup")
                    existing = None
                if existing is not None:
                    existing["duplicate"] = True
                    return existing

            result = pipeline.process(tmp_path)
            raw_text = str(result.pop("_raw_text", ""))
            durable_path = _store_original(tmp_path, result["sha256"], suffix)

            try:
                persistence = _persist_result(
                    filename=filename,
                    file_type=suffix.lstrip("."),
                    file_path=str(durable_path) if store_originals else "",
                    raw_text=raw_text,
                    result=result,
                    request=request,
                )
            except psycopg.Error as exc:
                request_id = _log_database_error(request, exc, "persist_result")
                persistence = {
                    "enabled": repository.enabled,
                    "stored": False,
                    "error": "database_unavailable",
                    "request_id": request_id,
                }

        persistence["forced_reprocess"] = force
        result["persistence"] = persistence
        if persistence.get("status"):
            result["status"] = persistence["status"]
        if repository.enabled and not persistence.get("stored"):
            result["status"] = "persistence_failed"
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise _database_http_exception(request, exc, "process_document") from exc
    except (RuntimeError, OSError) as exc:
        raise _service_http_exception(request, exc, "process_document") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@app.get("/api/v3/documents/by-sha/{sha256}", dependencies=[Depends(require_api_access)])
def get_document_by_sha(sha256: str, request: Request) -> dict:
    _require_database()
    try:
        result = get_latest_result_by_sha(repository, sha256)
    except psycopg.Error as exc:
        raise _database_http_exception(request, exc, "get_document_by_sha") from exc
    if not result:
        raise HTTPException(status_code=404, detail="document_not_found")
    return result


@app.get("/api/v3/documents/{document_id}", dependencies=[Depends(require_api_access)])
def get_document(document_id: str, request: Request) -> dict:
    _require_database()
    try:
        document = get_document_for_reprocess(repository, document_id)
    except psycopg.Error as exc:
        raise _database_http_exception(request, exc, "get_document") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_document_id") from exc
    if not document:
        raise HTTPException(status_code=404, detail="document_not_found")
    return document


@app.post("/api/v3/documents/{document_id}/reprocess", dependencies=[Depends(require_api_access)])
def reprocess_document(document_id: str, request: Request) -> dict:
    _require_database()
    try:
        document = get_document_for_reprocess(repository, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="document_not_found")
        stored_path = validate_stored_path(document, storage_dir)

        lock_factory = getattr(repository, "processing_lock", None)
        lock = lock_factory(str(document.get("sha256"))) if callable(lock_factory) else nullcontext()
        with lock:
            result = pipeline.process(stored_path)
            raw_text = str(result.pop("_raw_text", ""))
            persistence = _persist_result(
                filename=str(document.get("filename") or stored_path.name),
                file_type=str(document.get("file_type") or stored_path.suffix.lstrip(".")),
                file_path=str(stored_path),
                raw_text=raw_text,
                result=result,
                request=request,
            )
        persistence["forced_reprocess"] = True
        result["persistence"] = persistence
        if persistence.get("status"):
            result["status"] = persistence["status"]
        return result
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="stored_original_unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_reprocess_request") from exc
    except psycopg.Error as exc:
        raise _database_http_exception(request, exc, "reprocess_document") from exc
    except (RuntimeError, OSError) as exc:
        raise _service_http_exception(request, exc, "reprocess_document") from exc


@app.get("/api/v3/reviews", dependencies=[Depends(require_api_access)])
def list_reviews(
    request: Request,
    status: str = Query(default="pending", max_length=30),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    _require_database()
    try:
        return repository.list_reviews(status=status, limit=limit)
    except psycopg.Error as exc:
        raise _database_http_exception(request, exc, "list_reviews") from exc


@app.get("/api/v3/reviews/{review_id}", dependencies=[Depends(require_api_access)])
def get_review(review_id: str, request: Request) -> dict:
    _require_database()
    try:
        review = repository.get_review(review_id)
    except psycopg.Error as exc:
        raise _database_http_exception(request, exc, "get_review") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_review_id") from exc
    if not review:
        raise HTTPException(status_code=404, detail="review_not_found")
    return review


@app.post("/api/v3/reviews/{review_id}/complete", dependencies=[Depends(require_api_access)])
def complete_review(review_id: str, payload: ReviewCompleteRequest, request: Request) -> dict:
    _require_database()
    try:
        return repository.complete_review(
            review_id,
            action=payload.action,
            reviewer=payload.reviewer,
            review_data=payload.review_data,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateBusinessRecordError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ReviewDataValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise _database_http_exception(request, exc, "complete_review") from exc
