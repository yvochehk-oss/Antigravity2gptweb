from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

from .extractors import HybridExtractor
from .granite_client import GraniteAuditClient
from .ling_client import LingClient
from .ocr import PaddleOCRAdapter
from .parsers import DocumentParser
from .pipeline import IDPPipeline


app = FastAPI(title="成都建工 IDP V3.0", version="3.0.0")


def _enabled(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() not in {"0", "false", "no", "off"}


ling_enabled = _enabled("LING_ENABLED", "1")
ocr_enabled = _enabled("OCR_ENABLED", "1")
granite_enabled = _enabled("GRANITE_ENABLED", "0")

ling_client = LingClient() if ling_enabled else None
ocr_adapter = PaddleOCRAdapter() if ocr_enabled else None
granite_client = GraniteAuditClient() if granite_enabled else None

pipeline = IDPPipeline(
    DocumentParser(ocr_parser=ocr_adapter),
    HybridExtractor(ling_client),
    auditor=granite_client,
)


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
    }


@app.post("/api/v3/documents/process")
async def process_document(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "upload.bin").suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        raise HTTPException(status_code=415, detail="unsupported file type")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        return pipeline.process(tmp_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Parser/OCR runtime failures remain service errors. Ling/Granite model
        # failures are handled inside the pipeline and route to human review.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)
