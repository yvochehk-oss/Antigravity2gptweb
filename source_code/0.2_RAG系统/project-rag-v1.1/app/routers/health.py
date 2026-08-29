"""Health/status routes backed by a single dependency snapshot."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from sqlalchemy import func, select
from ..auth import require_web_auth
from ..db import SessionLocal
from ..health import collect_health_snapshot
from ..models import Chunk, Document, Project

router = APIRouter(tags=["meta"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


import logging

logger = logging.getLogger(__name__)


@router.get("/health", response_class=HTMLResponse)
def health_ui(request: Request, principal=Depends(require_web_auth)):
    snapshot = collect_health_snapshot()
    try:
        with SessionLocal() as db:
            indexed_chunks = db.scalar(select(func.count(Chunk.id))) or 0
            docs_count = db.scalar(select(func.count(Document.id))) or 0
            projects_count = db.scalar(select(func.count(Project.id))) or 0
    except Exception as exc:
        logger.warning("Failed to collect table counts for health UI: %s", exc, exc_info=True)
        indexed_chunks = 0
        docs_count = 0
        projects_count = 0

    return templates.TemplateResponse(
        request,
        "health.html",
        {
            "request": request,
            "health_data": snapshot,
            "indexed_chunks": indexed_chunks,
            "docs_count": docs_count,
            "projects_count": projects_count,
            "active_page": "health",
        },
    )


@router.get("/api/v1/health")
def health_api():
    return collect_health_snapshot()


@router.get("/healthz")
def healthz_root():
    snapshot = collect_health_snapshot()
    return {
        "status": snapshot["status"],
        "version": snapshot["version"],
        "request_id": snapshot["request_id"],
        "components": snapshot["components"],
        "checked_in_ms": snapshot["checked_in_ms"],
    }


def install_health_routes(app) -> None:
    """Replace legacy inline health endpoints without changing other routes."""
    paths = {"/health", "/api/v1/health", "/healthz"}
    app.router.routes[:] = [
        route for route in app.router.routes
        if getattr(route, "path", None) not in paths
    ]
    app.include_router(router)


__all__ = ["install_health_routes", "router"]
