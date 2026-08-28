"""Health/status routes backed by a single dependency snapshot."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..auth import require_web_auth
from ..health import collect_health_snapshot

router = APIRouter(tags=["meta"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


@router.get("/health", response_class=HTMLResponse)
def health_ui(request: Request, principal=Depends(require_web_auth)):
    snapshot = collect_health_snapshot()
    return templates.TemplateResponse(
        request,
        "health.html",
        {
            "request": request,
            "health_data": snapshot,
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
