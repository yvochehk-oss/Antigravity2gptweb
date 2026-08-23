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

from .config import AUTO_START_WORKER, IS_POSTGRES, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from .logging_config import setup_logging, get_logger
from .middleware import RateLimitMiddleware, RequestIdMiddleware
from .security import RAGSecurityMiddleware
from .services.jobs import start_worker, stop_worker
from .services.documents import scan_folder
from .services.retrieval import retrieve, get_query_stats
from .services.llm import answer_with_llm

setup_logging()
logger = get_logger(__name__)

BUSINESS_ROLE_META = {
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
    "construction": "construction", "construction_role": "construction",
    "trade": "trade", "labor": "labor", "equipment": "equipment",
    "施工": "construction", "施工企业": "construction",
    "商贸": "trade", "商贸物资": "trade",
    "劳务": "labor", "建筑劳务": "labor",
    "设备": "equipment", "机械租赁": "equipment", "工程设备": "equipment",
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


def now() -> str:
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# V1.1: v1.0 legacy import with graceful fallback
try:
    from facts_provider import setup_facts_provider
    from ai_review.routes import router as ai_review_router
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

    from app import models
    from ai_review import models as ai_review_models

    from .db import init_db
    init_db()
    logger.info("All models registered and tables created")

    from .config_validator import validate_config
    errors = validate_config()
    if errors:
        logger.warning(f"Configuration warnings: {errors}")

    from .session import get_db
    from .models import Project as RAGProject
    with get_db() as db:
        if not db.query(RAGProject).count():
            db.add(RAGProject(
                project_code="YB-DEMO-001",
                name="宜宾示范项目",
                external_system="construction-tax",
                external_project_id="1",
                note="ProjectRAG V1.1 示范知识空间",
                created_at=now(),
                updated_at=now()
            ))
            db.commit()
            logger.info("Created demo project YB-DEMO-001")

    # 预热本地 BGE-M3 与 BGE-Reranker 模型至内存，确保后续网页检索瞬间响应
    try:
        from .services.embeddings import embed
        from .services.reranker import rerank
        embed("预热系统")
        rerank("预热系统", [{"text": "预热样本"}], top_k=1)
        logger.info("Local BGE-M3 & Reranker models pre-warmed successfully")
    except Exception as _e:
        logger.warning(f"Model pre-warm warning: {_e}")

    if AUTO_START_WORKER:
        start_worker()
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
