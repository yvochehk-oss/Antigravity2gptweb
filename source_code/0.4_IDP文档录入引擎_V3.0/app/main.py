from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

from .extractors import HybridExtractor
from .ling_client import LingClient
from .parsers import DocumentParser
from .pipeline import IDPPipeline


app = FastAPI(title="成都建工 IDP V3.0", version="3.0.0")

ling_enabled = os.getenv("LING_ENABLED", "1").lower() not in {"0", "false", "no"}
ling_client = LingClient() if ling_enabled else None
pipeline = IDPPipeline(DocumentParser(), HybridExtractor(ling_client))


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "cdjg-idp",
        "version": "3.0.0",
        "semantic_model": ling_client.model if ling_client else None,
        "semantic_enabled": ling_enabled,
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
    except (RuntimeError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)
