"""V0.2: FastAPI app wiring - router, middleware, and static file registration.

Separated from main.py to reduce entry-point complexity and enable independent
testing of the app assembly without loading all endpoint logic.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config
from .middleware import AuthMiddleware
from .routers import ALL_ROUTERS


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    All router, middleware, and static-file wiring lives here so that
    ``app.main`` can be imported without triggering the full endpoint
    module graph during module-load-time checks.
    """
    app = FastAPI(
        title="建筑项目经营与税务统筹系统",
        version="2.5",
        description=(
            "V0.2: 确定性引擎 + AI 审查 + 整改闭环。"
            "AI 仅负责解释、审查和建议，不覆盖确定性数字。"
        ),
    )

    app.add_middleware(AuthMiddleware)

    for r in ALL_ROUTERS:
        app.include_router(r)

    _mount_static_assets(app)

    return app


def _mount_static_assets(app: FastAPI) -> None:
    """Mount Vite-built frontend assets when the dist directory exists."""
    static_dist = Path(__file__).resolve().parent / "static_dist"
    if static_dist.exists() and (static_dist / "assets").exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(static_dist / "assets")),
            name="assets",
        )
        app.mount(
            "/ui",
            StaticFiles(directory=str(static_dist), html=True),
            name="ui",
        )
