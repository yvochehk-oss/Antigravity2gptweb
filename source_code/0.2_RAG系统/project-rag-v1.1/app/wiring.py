"""FastAPI app wiring - router, middleware, and static file registration.

Separated from main.py to reduce entry-point complexity and enable independent
testing of the app assembly.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import AUTO_START_WORKER, RERANKER_ENABLED
from .logging_config import get_logger, setup_logging
from .middleware import RateLimitMiddleware, RequestIdMiddleware
from .security import RAGSecurityMiddleware
from .services.jobs import start_worker, stop_worker

setup_logging()
logger = get_logger(__name__)


def now() -> str:
    """Return the UTC timestamp format used by the main application.

    ``main.py`` imports this compatibility helper from the wiring module for
    audit/request records.  Keep the helper here so application assembly and
    the legacy entry point share one import path without reintroducing a
    second clock implementation.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

BUSINESS_ROLE_META = {
    "owner": {
        "label": "外部发包业主",
        "badge": "badge-blue",
        "color": "#60a5fa",
        "description": "项目发包方、业主投资平台与建设单位",
    },
    "construction": {
        "label": "建筑施工",
        "badge": "badge-cyan",
        "color": "#38bdf8",
        "description": "工程总承包、专业施工与项目履约业务",
    },
    "trade": {
        "label": "商贸物资",
        "badge": "badge-emerald",
        "color": "#34d399",
        "description": "建材采购、供应链贸易与货物交付业务",
    },
    "labor": {
        "label": "建筑劳务",
        "badge": "badge-purple",
        "color": "#c084fc",
        "description": "施工劳务、班组实名制与工资核算业务",
    },
    "equipment": {
        "label": "工程设备",
        "badge": "badge-amber",
        "color": "#fbbf24",
        "description": "施工机械租赁、设备进退场与台班结算业务",
    },
}

BUSINESS_ROLE_ALIASES = {
    "a": "construction", "b": "trade", "c": "labor", "d": "equipment",
    "ea": "construction", "eb": "trade", "ec": "labor", "ed": "equipment",
    "e0": "owner", "owner": "owner", "client": "owner",
    "subcontractor": "construction", "supplier": "trade", "partner": "owner", "vendor": "trade",
    "construction": "construction", "construction_role": "construction",
    "trade": "trade", "labor": "labor", "equipment": "equipment",
    "业主": "owner", "甲方": "owner", "外部业主": "owner", "发包方": "owner",
    "施工": "construction", "施工企业": "construction", "外部施工": "construction",
    "商贸": "trade", "商贸物资": "trade", "外部商贸": "trade", "材料供应": "trade",
    "劳务": "labor", "建筑劳务": "labor", "外部劳务": "labor",
    "设备": "equipment", "机械租赁": "equipment", "工程设备": "equipment", "外部机械": "equipment",
}


def _business_role_key(value):
    """Normalize a stored business-role value to a descriptive role key."""
    text = str(value or "").strip().lower()
    return BUSINESS_ROLE_ALIASES.get(text, text)


def _entity_summary(entities):
    """Summarize the current canonical roster without hard-coded counts."""
    legal = sum(1 for e in entities if e["legal_entity"])
    branches = sum(1 for e in entities if not e["legal_entity"])
    return {
        "total": len(entities),
        "legal": legal,
        "branches": branches,
        "label": f"{legal}家法人 + {branches}家分公司 · 按业务角色穿透",
    }


# V1.1: v1.0 legacy import with graceful fallback
try:
    from ai_review.routes import router as ai_review_router
    from facts_provider import setup_facts_provider
    _HAS_V1_LEGACY = True
except Exception as _e:
    logger.warning(f"facts_provider / ai_review 接入失败（不影响 v0.2 路径）: {_e}")
    _HAS_V1_LEGACY = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting ProjectRAG V1.1...")
    from .config_validator import ensure_directories
    ensure_directories()

    # Import model modules for SQLAlchemy mapper/table registration. These
    # imports are intentionally side-effect-only and must stay in lifespan so
    # the PostgreSQL schema is complete before init_db() runs.
    from ai_review import models as _ai_review_models  # noqa: F401
    from app import models as _app_models  # noqa: F401

    from .config_validator import validate_config
    errors = validate_config()
    if errors:
        raise RuntimeError(
            "ProjectRAG configuration validation failed: "
            + "; ".join(str(error) for error in errors)
        )

    from .db import init_db
    init_db()
    logger.info("PostgreSQL schema and pgvector prerequisites validated")

    # BGE-M3 is always required. The reranker remains disabled unless explicitly enabled.
    try:
        from .services.embeddings import embed

        embed("预热系统", raise_on_error=True)
        if RERANKER_ENABLED:
            from .services.reranker import rerank

            rerank("预热系统", [{"text": "预热样本"}], top_k=1, raise_on_error=True)
            logger.info("Local BGE-M3 and enabled reranker models pre-warmed successfully")
        else:
            logger.info("Local BGE-M3 model pre-warmed; reranker remains disabled")
    except Exception as _e:
        logger.exception("Local model pre-warm failed; refusing to start")
        raise RuntimeError("Local embedding model pre-warm failed") from _e

    if AUTO_START_WORKER:
        # Uvicorn owns SIGTERM/SIGINT and must receive those signals to enter
        # the FastAPI lifespan shutdown.  The worker is stopped explicitly by
        # the lifespan context below; registering a second process handler
        # here would replace Uvicorn's handler and leave the server alive.
        start_worker(install_signal_handlers=False)
        logger.info("Ingest worker auto-started")

    yield

    logger.info("Shutting down ProjectRAG...")
    stop_worker()
    from .db import close_connections
    close_connections()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    app = FastAPI(
        title="ProjectRAG",
        version="1.1.0",
        description="Project Knowledge RAG Service (merged v0.2-optimized + v1.0 facts/ai_review + v0.2 regulation retrieval)",
        lifespan=lifespan,
    )

    from starlette.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(RAGSecurityMiddleware)

    if _HAS_V1_LEGACY:
        try:
            setup_facts_provider(app)
            app.include_router(ai_review_router)
            logger.info("v1.0 legacy routes registered")
        except Exception as _e:
            logger.warning(f"v1.0 legacy routes 注册失败: {_e}")

    templates_path = Path(__file__).parent / "templates"
    if templates_path.exists():
        from fastapi.templating import Jinja2Templates
        app.state.templates = Jinja2Templates(directory=str(templates_path))

    static_path = Path(__file__).parent / "static"
    if static_path.exists():
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    return app
