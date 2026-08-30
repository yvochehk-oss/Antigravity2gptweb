"""V0.2: FastAPI app wiring - router, middleware, and avatar file registration.

Separated from main.py to reduce entry-point complexity and enable independent
testing of the app assembly without loading all endpoint logic.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .middleware import AuthMiddleware
from .routers import ALL_ROUTERS

_DEFAULT_CORS_ORIGINS = (
    "http://localhost:{port},http://127.0.0.1:{port},"
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:3000,http://127.0.0.1:3000"
)


def _parse_cors_origins(raw_origins: str) -> list[str]:
    """Return a strict explicit-origin allow-list for browser credentials.

    A wildcard is never safe together with cookies/credentials.  Rejecting a
    malformed or wildcard override during app construction is fail-closed and
    avoids silently broadening the browser trust boundary after deployment.
    """
    origins: list[str] = []
    for raw_origin in raw_origins.split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            raise ValueError(
                "TAX_CORS_ALLOW_ORIGINS must contain explicit origins; "
                "wildcard '*' is not allowed"
            )
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "TAX_CORS_ALLOW_ORIGINS entries must be absolute HTTP(S) origins"
            )
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(
                "TAX_CORS_ALLOW_ORIGINS contains an invalid port"
            ) from exc
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("TAX_CORS_ALLOW_ORIGINS contains an invalid port")
        if origin not in origins:
            origins.append(origin)
    if not origins:
        raise ValueError("TAX_CORS_ALLOW_ORIGINS must contain at least one origin")
    return origins


def _cors_origins() -> list[str]:
    """Read the Tax browser origin policy at application-construction time."""
    port = os.getenv("TAX_PORT", "8921").strip() or "8921"
    default_origins = _DEFAULT_CORS_ORIGINS.format(port=port)
    return _parse_cors_origins(
        os.getenv("TAX_CORS_ALLOW_ORIGINS", default_origins)
    )


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    All router, middleware, and avatar-file wiring lives here so that
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

    from starlette.middleware.cors import CORSMiddleware
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(AuthMiddleware)
    # Register CORS last so it is the outer browser boundary and can answer
    # authenticated API preflights before AuthMiddleware sees an OPTIONS call.
    # There is intentionally exactly one CORS layer for this application.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "X-CSRF-Token",
            "X-Request-ID",
            "X-XSRF-TOKEN",
        ],
    )

    for r in ALL_ROUTERS:
        app.include_router(r)

    _mount_static_assets(app)

    return app


def _mount_static_assets(app: FastAPI) -> None:
    """Mount Vite-built frontend assets and avatar uploads when the dist directory exists."""
    app_dir = Path(__file__).resolve().parent
    avatars_dir = app_dir / "data" / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/avatars",
        StaticFiles(directory=str(avatars_dir)),
        name="avatars",
    )
    static_dist = app_dir / "static_dist"
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
