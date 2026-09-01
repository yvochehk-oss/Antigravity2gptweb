"""FastAPI main application - ProjectRAG V1.1.

Imports app construction from wiring.py and health components from health.py.
Routes are registered via the lifespan context in wiring.py.
"""
import hashlib
import hmac
import json
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.orm import defer
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError

from .auth import (
    TaxPrincipal,
    require_web_auth,
    require_web_or_service_read,
    require_web_or_service_role,
    require_web_role,
)
from .config import AUTO_START_WORKER, DEFAULT_PAGE_SIZE, IS_POSTGRES, MAX_PAGE_SIZE
from .config_validator import validate_config
from .db import SessionLocal, db_health
from .logging_config import get_logger, setup_logging
from .middleware import RateLimitMiddleware, RequestIdMiddleware
from .models import (
    BenchmarkQuestion,
    BenchmarkRun,
    Chunk,
    Document,
    Entity,
    ExternalParty,
    IngestJob,
    Project,
    QueryLog,
    Regulation,
    RegulationArticle,
    RegulationChunk,
    is_canonical_entity_code,
)
from .domain.entities import get_external_preset, map_to_standard_external_code
from .observability import (
    get_request_id,
)
from .routers.adaptive_retrieval import router as adaptive_retrieval_router
from .routers.auth import router as auth_router
from .routers.executive_mobile import router as executive_mobile_router
from .routers.llm_models import router as llm_models_router
from .routers.mounts import router as mounts_router
from .routers.users import router as users_router
from .schemas import (
    DocumentMetadataPatch,
    EntityCreate,
    EntityPatch,
    ExternalPartyCreate,
    ExternalPartyPatch,
    FolderImportRequest,
    ProjectCreate,
    ProjectDeleteRequest,
    ProjectSync,
    QueryRequest,
    RegulationArticleCreate,
    RegulationCreate,
    RegulationQueryRequest,
    RegulationRetrieveRequest,
    RegulationUpdate,
    RetrieveRequest,
)
from .security import RAGSecurityMiddleware, read_upload_limited, require_same_origin, security_audit
from .services.documents import (
    purge_redundant_duplicate_documents,
    register_bytes,
    repair_filename_classifications,
    scan_folder,
)
from .services.embeddings import embedding_runtime
from .services.extractor import (
    ExtractionError,
    extract_contract_fields_from_text,
    extract_from_chunk,
    extract_invoice_fields_from_text,
    extract_payment_fields_from_text,
    llm_extraction_available,
    validate_invoice_fields,
)
from .services.jobs import enqueue_parse, get_worker_status, process_next
from .services.llm import answer_with_llm
from .services.regulation_retrieval import (
    answer_regulation_query,
    retrieve_regulation_articles,
    retrieve_regulations,
)
from .services.reranker import reranker_runtime
from .services.retrieval import get_query_stats, retrieve
from .services.storage import PathTraversalError, StorageError, validate_stored_file
from .services.storage.write import (
    finalize_document_cleanup,
    restore_document_cleanup,
    stage_document_cleanup,
)
from .services.tax_extraction import (
    EXTRACT_DOC_TYPE_FILTERS,
    EXTRACT_QUERY_TEMPLATES,
    ExtractedItem,
    ExtractTaxRequest,
    ExtractTaxResponse,
)
from .session import get_db
from .user_center.db import UserCenterSessionLocal
from .user_center.models import UserAccount
from .user_center.security import verify_password
from .validation_errors import request_validation_exception_handler
from .wiring import (
    BUSINESS_ROLE_META,
    _business_role_key,
    lifespan,
    now,
)


def _tax_extract_workers() -> int:
    """Bound parallel LLM extraction without overwhelming local small models."""
    try:
        configured = int(os.getenv("PROJECT_RAG_TAX_EXTRACT_CONCURRENCY", "8"))
    except ValueError:
        configured = 8
    return max(1, min(configured, 8))

# Import v1.0 legacy modules
try:
    from ai_review.routes import router as ai_review_router
    from facts_provider import setup_facts_provider
    _HAS_V1_LEGACY = True
except Exception:
    _HAS_V1_LEGACY = False

setup_logging()
logger = get_logger(__name__)


def _canonical_entity_views(db) -> list[dict]:
    """Return active canonical master rows (26 internal entities + external parties) for UI selectors and cards."""
    rows = db.execute(
        select(Entity)
        .where(
            Entity.entity_code.is_not(None),
            func.lower(Entity.status) == "active",
        )
        .order_by(Entity.entity_code)
    ).scalars().all()

    views = []
    for entity in rows:
        code = (entity.entity_code or "").strip().upper()
        if not is_canonical_entity_code(code):
            continue
        role = _business_role_key(entity.business_role)
        role_meta = BUSINESS_ROLE_META.get(role, {
            "label": "未分类业务",
            "badge": "badge-muted",
            "color": "#94a3b8",
            "description": "待补充业务角色",
        })
        views.append({
            "entity_code": code,
            "name": entity.name,
            "short_name": entity.short_name or entity.name,
            "business_role": role,
            "business_role_label": role_meta["label"],
            "business_role_badge": role_meta["badge"],
            "business_role_color": role_meta["color"],
            "business_role_description": role_meta["description"],
            "legal_entity": bool(entity.legal_entity),
            "parent_entity_code": entity.parent_entity_code,
            "tax_id": entity.tax_id or entity.unified_social_credit_code or "",
            "industry": entity.industry or "",
            "source": "系统内",
            "is_external": False,
            "legal_representative": entity.legal_representative or "-",
            "registered_capital": entity.registered_capital or "-",
            "note": entity.note or "",
        })

    ext_rows = db.execute(
        select(ExternalParty)
        .where(ExternalParty.active.is_(True))
        .order_by(ExternalParty.code)
    ).scalars().all()

    for ext in ext_rows:
        code = (ext.code or "").strip().upper()
        preset = get_external_preset(code)
        if preset:
            role = preset["business_role"]
            name = ext.name if (ext.name and ext.name not in (code, f"系统外合作单位 ({code})", f"外部协作单位({code})")) else preset["name"]
            short_name = ext.short_name or preset.get("short_name") or name
            tax_id = ext.tax_id or preset.get("tax_id") or ""
            note = preset.get("note") or f"系统外合作单位 (代码: {code})"
        else:
            role = _business_role_key(ext.kind)
            name = ext.name or code
            short_name = ext.short_name or name
            tax_id = ext.tax_id or ""
            note = f"系统外合作单位 (代码: {code})"
        role_meta = BUSINESS_ROLE_META.get(role, {
            "label": "系统外合作方",
            "badge": "badge-purple",
            "color": "#a855f7",
            "description": "系统外合格供应商/业主单位/合作分包",
        })
        views.append({
            "entity_code": code,
            "name": name,
            "short_name": short_name,
            "business_role": role,
            "business_role_label": role_meta["label"],
            "business_role_badge": "badge-purple",
            "business_role_color": "#a855f7",
            "business_role_description": note,
            "legal_entity": True,
            "parent_entity_code": None,
            "tax_id": tax_id,
            "industry": "外部往来",
            "source": "系统外",
            "is_external": True,
            "legal_representative": "-",
            "registered_capital": "-",
            "note": note,
        })

    return views


def _entity_summary(entities: list[dict]) -> dict:
    """Summarize the current canonical roster."""
    internal = sum(1 for e in entities if not e.get("is_external"))
    external = sum(1 for e in entities if e.get("is_external"))
    legal = sum(1 for e in entities if e["legal_entity"])
    branches = sum(1 for e in entities if not e["legal_entity"])
    return {
        "total": len(entities),
        "internal": internal,
        "external": external,
        "legal": legal,
        "branches": branches,
        "label": f"系统内 {internal} 家 + 系统外 {external} 家（共 {len(entities)} 家）",
    }


app = FastAPI(
    title="ProjectRAG",
    version="1.1.0",
    description="Project Knowledge RAG Service (merged v0.2-optimized + v1.0 facts/ai_review + v0.2 regulation retrieval)",
    lifespan=lifespan,
)
app.add_exception_handler(RequestValidationError, request_validation_exception_handler)

_DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:8922,http://localhost:8921"
)


def _parse_cors_origins(raw_origins: str) -> list[str]:
    """Parse explicit CORS origins and reject unsafe wildcard configuration."""
    origins = []
    for raw_origin in raw_origins.split(","):
        origin = raw_origin.strip()
        if not origin:
            continue
        if origin == "*":
            raise ValueError(
                "RAG_CORS_ALLOW_ORIGINS must contain explicit origins; wildcard '*' is not allowed"
            )
        origins.append(origin)
    return origins


_CORS_ALLOW_ORIGINS = _parse_cors_origins(
    os.getenv("RAG_CORS_ALLOW_ORIGINS", _DEFAULT_CORS_ORIGINS)
)

from starlette.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(RAGSecurityMiddleware)

app.include_router(auth_router)
app.include_router(mounts_router)
app.include_router(adaptive_retrieval_router)
app.include_router(executive_mobile_router)
app.include_router(llm_models_router)
app.include_router(users_router)

if _HAS_V1_LEGACY:
    try:
        setup_facts_provider(app)
        app.include_router(ai_review_router)
        logger.info("v1.0 legacy routes registered")
    except Exception as _e:
        logger.warning(f"v1.0 legacy routes 注册失败: {_e}")


from .services.metadata import (
    get_document_type_display_name,
    get_category_display_name,
    get_invoice_type_display_name,
    format_parse_message,
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["doc_type_name"] = get_document_type_display_name
templates.env.filters["category_name"] = get_category_display_name
templates.env.filters["invoice_type_name"] = get_invoice_type_display_name
templates.env.filters["friendly_parse_msg"] = format_parse_message
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


def _check_db_component() -> dict[str, object]:
    started = _time.monotonic()
    try:
        from sqlalchemy import text
        from .db import SessionLocal
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {"status": "down", "latency_ms": latency_ms, "error": str(exc)}


def _check_worker_component() -> dict[str, object]:
    status = get_worker_status()
    if status.get("running"):
        return {"status": "ok", "thread_id": status.get("thread_id"), "thread_name": status.get("thread_name")}
    if bool(AUTO_START_WORKER):
        return {"status": "down", "error": "worker 未运行（auto_start 已启用）"}
    return {"status": "ok", "auto_start": False}


def _check_ai_component() -> dict[str, object]:
    return {"status": "ok" if llm_extraction_available() else "degraded", "extraction_available": llm_extraction_available()}


def _check_facts_component() -> dict[str, object]:
    try:
        from sqlalchemy import text
        from facts_provider import facts_provider as _fp
        from .db import SessionLocal
        with SessionLocal() as db:
            row = db.execute(text("SELECT 1 FROM analytics_project_full LIMIT 1")).first()
        if row is None:
            return {"status": "degraded", "module": _fp.__name__, "source": "analytics_project_full", "error": "analytics source has no rows"}
        return {"status": "ok", "module": _fp.__name__, "source": "analytics_project_full"}
    except Exception as exc:
        return {"status": "degraded", "source": "analytics_project_full", "error": f"Facts source unavailable: {exc}"}


def _check_embedding_component() -> dict[str, object]:
    runtime = embedding_runtime()
    backend = str(runtime.get("backend") or "")
    if backend == "hash_v1":
        runtime.update({"status": "degraded", "error": "development hash embedding backend is active"})
    elif backend == "bge_m3":
        runtime["status"] = "ok" if runtime.get("model_loaded") else "degraded"
        if not runtime.get("model_loaded"):
            runtime["error"] = "BGE model is configured but not loaded"
    else:
        runtime.update({"status": "down", "error": f"unsupported embedding backend: {backend or '<empty>'}"})
    return runtime


def _check_reranker_component() -> dict[str, object]:
    runtime = reranker_runtime()
    backend = str(runtime.get("backend") or "")
    if backend in {"", "none", "off"}:
        runtime.update({"status": "ok", "disabled": True})
    elif backend == "bge_v2_m3":
        runtime["status"] = "ok" if runtime.get("model_loaded") else "degraded"
        if not runtime.get("model_loaded"):
            runtime["error"] = "reranker model is configured but not loaded"
    else:
        runtime.update({"status": "down", "error": f"unsupported reranker backend: {backend}"})
    return runtime


def _health_components() -> dict[str, dict[str, object]]:
    return {
        "db": _check_db_component(),
        "worker": _check_worker_component(),
        "ai": _check_ai_component(),
        "facts": _check_facts_component(),
        "embedding": _check_embedding_component(),
        "reranker": _check_reranker_component(),
    }


@app.get("/health", response_class=HTMLResponse)
def health_ui(request: Request, principal=Depends(require_web_auth)):
    data = health()
    return templates.TemplateResponse(request, "health.html", {"request": request, "health_data": data, "active_page": "health"})


@app.get("/api/v1/health")
def health():
    started = _time.monotonic()
    validation_errors = validate_config()
    components = _health_components()
    priority = {"ok": 0, "degraded": 1, "down": 2}
    worst = "ok"
    for component in components.values():
        status = str(component.get("status", "down"))
        if priority.get(status, 2) > priority[worst]:
            worst = status
    return {
        "status": "ok" if not validation_errors and worst == "ok" else worst,
        "service": "project-rag",
        "version": "1.1.0",
        "request_id": get_request_id(),
        "native_parser_available": True,
        "database": db_health(),
        "embedding": embedding_runtime(),
        "reranker": reranker_runtime(),
        "llm_extraction": llm_extraction_available(),
        "worker": get_worker_status(),
        "worker_auto_start": AUTO_START_WORKER,
        "validation_errors": validation_errors,
        "components": components,
        "checked_in_ms": int((_time.monotonic() - started) * 1000),
    }


@app.get("/healthz", tags=["meta"])
def healthz_root() -> dict[str, object]:
    started = _time.monotonic()
    components = _health_components()
    priority = {"ok": 0, "degraded": 1, "down": 2}
    worst = "ok"
    for component in components.values():
        status = str(component.get("status", "down"))
        if priority.get(status, 2) > priority[worst]:
            worst = status
    return {
        "status": worst,
        "version": "1.1.0",
        "request_id": get_request_id(),
        "components": components,
        "checked_in_ms": int((_time.monotonic() - started) * 1000),
    }


@app.get("/api/v1/projects")
def api_projects():
    with get_db() as db:
        rows = db.execute(select(Project).order_by(Project.id)).scalars().all()
        out = [{
            "id": x.id,
            "project_code": x.project_code,
            "name": x.name,
            "external_system": x.external_system,
            "external_project_id": x.external_project_id,
            "status": x.status,
            "created_at": getattr(x, "created_at", "")
        } for x in rows]
    return out


@app.post("/api/v1/projects")
def api_create_project(body: ProjectCreate):
    with get_db() as db:
        if db.scalar(select(Project).where(Project.project_code == body.project_code)):
            raise HTTPException(409, "project_code already exists")
        p = Project(**body.model_dump(), created_at=now(), updated_at=now())
        db.add(p)
        db.commit()
        db.refresh(p)
        return {"id": p.id, "project_code": p.project_code, "name": p.name}


@app.post("/api/v1/projects/sync")
def api_sync_project(body: ProjectSync):
    with get_db() as db:
        p = None
        if body.external_system and body.external_project_id:
            p = db.scalar(select(Project).where(Project.external_system == body.external_system, Project.external_project_id == body.external_project_id))
        if not p:
            p = db.scalar(select(Project).where(Project.project_code == body.project_code))
        if not p:
            p = Project(**body.model_dump(), created_at=now(), updated_at=now())
            db.add(p)
        else:
            for k, v in body.model_dump().items():
                setattr(p, k, v)
            p.updated_at = now()
        db.commit()
        db.refresh(p)
        return {"id": p.id, "project_code": p.project_code, "name": p.name, "status": p.status}


@app.get("/api/v1/projects/{project_id}")
def api_project(project_id: int, page: int = Query(1, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)):
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")
        total = db.scalar(select(func.count(Document.id)).where(Document.project_id == project_id, Document.duplicate_of_id.is_(None))) or 0
        offset = (page - 1) * page_size
        docs = db.execute(select(Document).where(Document.project_id == project_id, Document.duplicate_of_id.is_(None)).order_by(Document.id.desc()).offset(offset).limit(page_size)).scalars().all()
        total_pages = (total + page_size - 1) // page_size
        return {
            "id": p.id, "project_code": p.project_code, "name": p.name, "status": p.status,
            "pagination": {"page": page, "page_size": page_size, "total_items": total, "total_pages": total_pages, "has_next": page < total_pages, "has_prev": page > 1},
            "documents": [{"id": d.id, "document_code": d.document_code, "filename": d.filename, "parse_status": d.parse_status, "document_type": d.document_type, "business_category": d.business_category} for d in docs]
        }


@app.get("/api/v1/documents/{document_id}")
def api_document(document_id: int):
    with get_db() as db:
        d = db.get(Document, document_id)
        if not d:
            raise HTTPException(404, "document not found")
        n = db.scalar(select(func.count(Chunk.id)).where(Chunk.document_id == document_id)) or 0
        out = {k: getattr(d, k) for k in ["id", "project_id", "document_code", "filename", "file_type", "file_hash", "size_bytes", "parse_status", "parse_message", "document_type", "entity_code", "counterparty_code", "business_category", "contract_no", "period", "document_date", "confidentiality", "version_label", "version_status", "duplicate_of_id", "metadata_confidence", "metadata_source", "parse_attempts"]}
        out["chunk_count"] = n
        return out


@app.patch("/api/v1/documents/{document_id}/metadata")
def api_patch_metadata(document_id: int, body: DocumentMetadataPatch):
    """Update document metadata."""
    with get_db() as db:
        d = db.get(Document, document_id)
        if not d:
            raise HTTPException(404, "document not found")
        changes = body.model_dump(exclude_none=True)
        if "counterparty_code" in changes:
            changes["counterparty_code"] = (
                map_to_standard_external_code(changes["counterparty_code"]) or ""
            )
        for k, v in changes.items():
            setattr(d, k, v)
        d.metadata_source = "user"
        d.metadata_confidence = 1.0
        d.updated_at = now()
        db.commit()
        db.refresh(d)
        logger.info(f"Updated metadata for document {document_id}")
        return {"id": d.id, "document_code": d.document_code, "document_type": d.document_type, "entity_code": d.entity_code, "counterparty_code": d.counterparty_code, "business_category": d.business_category, "period": d.period}


@app.post("/api/v1/documents/{document_id}/parse")
def api_parse(document_id: int):
    with get_db() as db:
        d = db.get(Document, document_id)
        if not d:
            raise HTTPException(404, "document not found")
        if d.duplicate_of_id:
            raise HTTPException(409, "duplicate document is not parsed independently")
        d.parse_status = "QUEUED"
        db.commit()
        job = enqueue_parse(db, d.id)
        return {"id": d.id, "parse_status": d.parse_status, "job_id": job.id}


@app.get("/api/v1/external-parties")
def api_external_parties(q: str = ""):
    with get_db() as db:
        stmt = select(ExternalParty).order_by(ExternalParty.code)
        if q:
            stmt = stmt.where(ExternalParty.name.ilike(f"%{q}%") | ExternalParty.short_name.ilike(f"%{q}%"))
        rows = db.execute(stmt).scalars().all()
        return [{"id": x.id, "code": x.code, "name": x.name, "short_name": x.short_name, "kind": x.kind, "tax_id": x.tax_id, "active": x.active} for x in rows]


@app.post("/api/v1/external-parties")
def api_create_external_party(body: ExternalPartyCreate):
    with get_db() as db:
        payload = body.model_dump()
        requested_code = payload["code"].strip().upper()
        canonical_code = map_to_standard_external_code(requested_code) or requested_code
        if canonical_code != requested_code:
            raise HTTPException(409, f"{requested_code} is an alias of canonical external party {canonical_code}; use {canonical_code}")
        payload["code"] = canonical_code
        if db.scalar(select(ExternalParty).where(ExternalParty.code == canonical_code)):
            raise HTTPException(409, "external party code already exists")
        party = ExternalParty(**payload)
        db.add(party)
        db.commit()
        db.refresh(party)
        return {"id": party.id, "code": party.code, "name": party.name}


@app.patch("/api/v1/external-parties/{party_id}")
def api_patch_external_party(party_id: int, body: ExternalPartyPatch):
    with get_db() as db:
        party = db.get(ExternalParty, party_id)
        if not party:
            raise HTTPException(404, "external party not found")
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(party, key, value)
        db.commit()
        db.refresh(party)
        return {"id": party.id, "code": party.code, "name": party.name, "active": party.active}


@app.get("/api/v1/entities")
def api_entities(q: str = Query("", description="Search by name"), industry: str = Query("", description="Filter by industry"), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    with get_db() as db:
        stmt = select(Entity).order_by(Entity.id)
        if q:
            stmt = stmt.where(Entity.name.ilike(f"%{q}%") | Entity.short_name.ilike(f"%{q}%"))
        if industry:
            stmt = stmt.where(Entity.industry == industry)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        offset = (page - 1) * page_size
        rows = db.execute(stmt.offset(offset).limit(page_size)).scalars().all()
        total_pages = (total + page_size - 1) // page_size if total else 0
        return {"page": page, "page_size": page_size, "total_items": total, "total_pages": total_pages, "has_next": page < total_pages, "has_prev": page > 1, "items": [{"id": e.id, "entity_code": e.entity_code, "name": e.name, "short_name": e.short_name, "entity_type": e.entity_type, "industry": e.industry, "business_role": e.business_role, "legal_entity": e.legal_entity, "status": e.status, "tax_id": e.tax_id, "legal_representative": e.legal_representative, "registered_capital": e.registered_capital, "establishment_date": e.establishment_date, "registration_authority": e.registration_authority, "unified_social_credit_code": e.unified_social_credit_code, "parent_entity_code": e.parent_entity_code, "source": e.source, "created_at": e.created_at} for e in rows]}


@app.get("/api/v1/entities/summary")
def api_entities_summary():
    with get_db() as db:
        rows = db.execute(select(Entity.industry, func.count(Entity.id)).group_by(Entity.industry).order_by(Entity.industry)).all()
        total = db.scalar(select(func.count(Entity.id))) or 0
        return {"total": total, "by_industry": [{"industry": r[0] or "未分类", "count": r[1]} for r in rows]}


@app.get("/api/v1/entities/{entity_id}")
def api_entity(entity_id: int):
    with get_db() as db:
        e = db.get(Entity, entity_id)
        if not e:
            raise HTTPException(404, "entity not found")
        return {"id": e.id, "name": e.name, "short_name": e.short_name, "entity_type": e.entity_type, "industry": e.industry}


@app.post("/api/v1/entities")
def api_create_entity(body: EntityCreate):
    with get_db() as db:
        if db.scalar(select(Entity).where(Entity.entity_code == body.entity_code)):
            raise HTTPException(409, "entity_code already exists")
        if db.scalar(select(Entity).where(Entity.name == body.name)):
            raise HTTPException(409, "entity with this name already exists")
        e = Entity(**body.model_dump(), created_at=now(), updated_at=now())
        db.add(e)
        db.commit()
        db.refresh(e)
        return {"id": e.id, "name": e.name, "industry": e.industry}


@app.patch("/api/v1/entities/{entity_id}")
def api_patch_entity(entity_id: int, body: EntityPatch):
    with get_db() as db:
        e = db.get(Entity, entity_id)
        if not e:
            raise HTTPException(404, "entity not found")
        changes = body.model_dump(exclude_none=True)
        if "entity_code" in changes and changes["entity_code"] != e.entity_code:
            raise HTTPException(409, "entity_code is immutable; create/migrate a master-data record instead")
        for k, v in changes.items():
            setattr(e, k, v)
        if not is_canonical_entity_code(e.entity_code):
            raise HTTPException(422, "internal entity must retain a canonical entity_code")
        expected_role = e.entity_code[0]
        if e.business_role != expected_role:
            raise HTTPException(422, f"business_role must match entity_code prefix {expected_role}")
        if e.entity_code == "A04" and (e.legal_entity or e.parent_entity_code != "A03"):
            raise HTTPException(422, "A04 must remain the non-legal branch of A03")
        e.updated_at = now()
        db.commit()
        db.refresh(e)
        return {"id": e.id, "name": e.name, "industry": e.industry}


@app.post("/api/v1/entities/batch")
def api_batch_create_entities(body: list[EntityCreate]):
    with get_db() as db:
        results = []
        for item in body:
            existing = db.scalar(select(Entity).where(Entity.entity_code == item.entity_code))
            payload = item.model_dump()
            if existing:
                for k, v in payload.items():
                    if k != "entity_code":
                        setattr(existing, k, v)
                existing.updated_at = now()
                results.append({"id": existing.id, "entity_code": existing.entity_code, "name": existing.name, "action": "updated"})
            else:
                entity = Entity(**payload, created_at=now(), updated_at=now())
                db.add(entity)
                results.append({"entity_code": item.entity_code, "name": item.name, "action": "created"})
        db.commit()
        return {"total": len(results), "items": results}


@app.post("/api/v1/entities/import-file")
async def api_import_entities_file(file: UploadFile = File(...), principal=Depends(require_web_or_service_role("admin"))):
    del principal
    from .services.entity_importer import import_entities_from_file_bytes
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件内容为空")
    with get_db() as db:
        try:
            return import_entities_from_file_bytes(db, content, file.filename or "file.xlsx")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("Failed to parse and import entities from file %s", file.filename)
            raise HTTPException(status_code=500, detail=f"文件解析失败: {exc}")


@app.post("/api/v1/retrieve")
def api_retrieve(body: RetrieveRequest):
    with get_db() as db:
        pid = _resolve_project(db, body.project_id, body.project_code)
        out = retrieve(db, pid, body.query, body.filters, body.top_k, body.rerank)
        return {"project_id": pid, "query": body.query, "results": out}


@app.post("/api/v1/query")
def api_query(body: QueryRequest):
    with get_db() as db:
        pid = _resolve_project(db, body.project_id, body.project_code)
        evidence = retrieve(db, pid, body.query, body.filters, body.top_k, body.rerank)
    answer = answer_with_llm(body.query, evidence) if body.answer else ""
    return {"project_id": pid, "query": body.query, "answer": answer, "citations": [], "results": evidence}


@app.get("/api/v1/stats")
def api_stats(project_id: Optional[int] = None):
    with get_db() as db:
        return get_query_stats(db, project_id)


def _resolve_project(db, project_id, project_code):
    p = db.get(Project, project_id) if project_id else db.scalar(select(Project).where(Project.project_code == project_code)) if project_code else None
    if not p:
        raise HTTPException(404, "project not found")
    return p.id


@app.get("/entities", response_class=HTMLResponse)
def web_entities(request: Request, principal=Depends(require_web_auth)):
    with get_db() as db:
        entities = _canonical_entity_views(db)
        return templates.TemplateResponse(request, "entities.html", {"entities": entities, "entity_summary": _entity_summary(entities), "active_page": "entities", "postgres": IS_POSTGRES})
