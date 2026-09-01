"""Read-only Canonical Facts endpoints for the Tax deterministic engine."""
from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..services.canonical_ssot import build_consolidated_project_pnl, ssot_status

router = APIRouter(prefix="/api/v1/canonical-ssot", tags=["canonical-ssot"])


@router.get("/status")
def canonical_ssot_status() -> dict:
    try:
        with SessionLocal() as db:
            return {"mode": "direct-read", "source_of_truth": "RAG canonical_facts", **ssot_status(db)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"canonical facts unavailable: {exc}") from exc


@router.get("/projects/{project_id}/consolidated-pnl")
def canonical_consolidated_pnl(project_id: int) -> dict:
    try:
        with SessionLocal() as db:
            return build_consolidated_project_pnl(db, project_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"canonical P&L unavailable: {exc}") from exc
