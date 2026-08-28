from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Literal, Optional

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
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
from .repository import DuplicateBusinessRecordError, IDPRepository


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="成都建工 IDP V3.0", version="3.0.0")


def _enabled(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() not in {"0", "false", "no", "off"}


class ReviewCompleteRequest(BaseModel):
    action: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=200)
    review_data: Optional[Dict[str, Any]] = None


ling_enabled = _enabled("LING_ENABLED", "1")
ocr_enabled = _enabled("OCR_ENABLED", "1")
granite_enabled = _enabled("GRANITE_ENABLED", "0")
store_originals = _enabled("STORE_ORIGINALS", "1")
storage_dir = Path(os.getenv("IDP_STORAGE_DIR", "./storage/originals"))

ling_client = LingClient() if ling_enabled else None
ocr_adapter = PaddleOCRAdapter() if ocr_enabled else None
granite_client = GraniteAuditClient() if granite_enabled else None
repository = IDPRepository()

pipeline = IDPPipeline(
    DocumentParser(ocr_parser=ocr_adapter),
    HybridExtractor(ling_client),
    auditor=granite_client,
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
            persistence["supersede_error"] = f"{type(exc).__name__}: {exc}"
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
    }


@app.post("/api/v3/documents/process")
async def process_document(
    file: UploadFile = File(...),
    force: bool = Query(default=False, description="Reprocess an identical SHA instead of returning its latest result"),
) -> dict:
    filename = file.filename or "upload.bin"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        raise HTTPException(status_code=415, detail="unsupported file type")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        sha256 = pipeline.sha256(tmp_path)
        if repository.enabled and not force:
            try:
                existing = get_latest_result_by_sha(repository, sha256)
            except psycopg.Error:
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
            )
        except psycopg.Error as exc:
            persistence = {
                "enabled": repository.enabled,
                "stored": False,
                "error": f"{type(exc).__name__}: {exc}",
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
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/api/v3/documents/by-sha/{sha256}")
def get_document_by_sha(sha256: str) -> dict:
    _require_database()
    try:
        result = get_latest_result_by_sha(repository, sha256)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="document_not_found")
    return result


@app.get("/api/v3/documents/{document_id}")
def get_document(document_id: str) -> dict:
    _require_database()
    try:
        document = get_document_for_reprocess(repository, document_id)
    except (psycopg.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not document:
        raise HTTPException(status_code=404, detail="document_not_found")
    return document


@app.post("/api/v3/documents/{document_id}/reprocess")
def reprocess_document(document_id: str) -> dict:
    _require_database()
    try:
        document = get_document_for_reprocess(repository, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="document_not_found")
        stored_path = validate_stored_path(document)

        result = pipeline.process(stored_path)
        raw_text = str(result.pop("_raw_text", ""))
        persistence = _persist_result(
            filename=str(document.get("filename") or stored_path.name),
            file_type=str(document.get("file_type") or stored_path.suffix.lstrip(".")),
            file_path=str(stored_path),
            raw_text=raw_text,
            result=result,
        )
        persistence["forced_reprocess"] = True
        result["persistence"] = persistence
        if persistence.get("status"):
            result["status"] = persistence["status"]
        return result
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v3/reviews")
def list_reviews(
    status: str = Query(default="pending", max_length=30),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    _require_database()
    return repository.list_reviews(status=status, limit=limit)


@app.get("/api/v3/reviews/{review_id}")
def get_review(review_id: str) -> dict:
    _require_database()
    try:
        review = repository.get_review(review_id)
    except (psycopg.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not review:
        raise HTTPException(status_code=404, detail="review_not_found")
    return review


@app.post("/api/v3/reviews/{review_id}/complete")
def complete_review(review_id: str, payload: ReviewCompleteRequest) -> dict:
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
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
