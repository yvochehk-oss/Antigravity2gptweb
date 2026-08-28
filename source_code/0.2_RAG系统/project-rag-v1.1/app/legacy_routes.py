"""FastAPI main application - ProjectRAG V1.1.

Imports app construction from wiring.py and health components from health.py.
Routes are registered via the lifespan context in wiring.py.
"""
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
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy import delete as sa_delete

from .auth import (
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
    Chunk,
    Document,
    Entity,
    ExternalParty,
    IngestJob,
    Project,
    Regulation,
    RegulationArticle,
    RegulationChunk,
    is_canonical_entity_code,
)
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
from .services.documents import register_bytes, scan_folder
from .services.embeddings import embedding_runtime
from .services.extractor import ExtractionError, extract_from_chunk, llm_extraction_available
from .services.jobs import enqueue_parse, get_worker_status, process_next
from .services.llm import answer_with_llm
from .services.mineru_adapter import mineru_available
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

    # Also load from dedicated external_parties table (all external units)
    ext_rows = db.execute(
        select(ExternalParty)
        .where(ExternalParty.active.is_(True))
        .order_by(ExternalParty.code)
    ).scalars().all()

    for ext in ext_rows:
        code = (ext.code or "").strip().upper()
        role = _business_role_key(ext.kind)
        role_meta = BUSINESS_ROLE_META.get(role, {
            "label": "系统外合作方",
            "badge": "badge-purple",
            "color": "#a855f7",
            "description": "系统外合格供应商/业主单位/合作分包",
        })
        views.append({
            "entity_code": code,
            "name": ext.name,
            "short_name": ext.short_name or ext.name,
            "business_role": role,
            "business_role_label": role_meta["label"],
            "business_role_badge": "badge-purple",
            "business_role_color": "#a855f7",
            "business_role_description": "系统外合作单位 / 业主单位 / 外部专业分包及供应商",
            "legal_entity": True,
            "parent_entity_code": None,
            "tax_id": ext.tax_id or "",
            "industry": "外部往来",
            "source": "系统外",
            "is_external": True,
            "legal_representative": "-",
            "registered_capital": "-",
            "note": f"系统外合作单位 (代码: {code})",
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


from .services.metadata import get_document_type_display_name, get_category_display_name
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["doc_type_name"] = get_document_type_display_name
templates.env.filters["category_name"] = get_category_display_name
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ============================================
# Health & Status Endpoints
# ============================================

def _check_db_component() -> dict[str, object]:
    """Return the database component health."""
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
    """Surface worker liveness."""
    status = get_worker_status()
    if status.get("running"):
        return {
            "status": "ok",
            "thread_id": status.get("thread_id"),
            "thread_name": status.get("thread_name"),
        }
    if bool(AUTO_START_WORKER):
        return {"status": "down", "error": "worker 未运行（auto_start 已启用）"}
    return {"status": "ok", "auto_start": False}


def _check_ai_component() -> dict[str, object]:
    """Surface whether structured LLM extraction is available."""
    return {
        "status": "ok" if llm_extraction_available() else "degraded",
        "extraction_available": llm_extraction_available(),
    }


def _check_facts_component() -> dict[str, object]:
    """Probe the Facts provider and its deterministic source view.

    Importing the Python module alone only proves that code is installed; it
    does not prove that Canonical Facts can be read.  The health result must
    therefore distinguish an unavailable analytics view from an available
    module with no source rows.
    """
    try:
        from sqlalchemy import text

        from facts_provider import facts_provider as _fp

        from .db import SessionLocal

        with SessionLocal() as db:
            row = db.execute(
                text("SELECT 1 FROM analytics_project_full LIMIT 1")
            ).first()
        if row is None:
            return {
                "status": "degraded",
                "module": _fp.__name__,
                "source": "analytics_project_full",
                "error": "analytics source has no rows",
            }
        return {
            "status": "ok",
            "module": _fp.__name__,
            "source": "analytics_project_full",
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "source": "analytics_project_full",
            "error": f"Facts source unavailable: {exc}",
        }


def _check_embedding_component() -> dict[str, object]:
    """Report embedding backend state without loading a large model."""
    runtime = embedding_runtime()
    backend = str(runtime.get("backend") or "")
    if backend == "hash_v1":
        runtime.update({
            "status": "degraded",
            "error": "development hash embedding backend is active",
        })
    elif backend == "bge_m3":
        runtime["status"] = "ok" if runtime.get("model_loaded") else "degraded"
        if not runtime.get("model_loaded"):
            runtime["error"] = "BGE model is configured but not loaded"
    else:
        runtime.update({
            "status": "down",
            "error": f"unsupported embedding backend: {backend or '<empty>'}",
        })
    return runtime


def _check_reranker_component() -> dict[str, object]:
    """Report reranker state; an explicit ``off`` backend is healthy."""
    runtime = reranker_runtime()
    backend = str(runtime.get("backend") or "")
    if backend in {"", "none", "off"}:
        runtime.update({"status": "ok", "disabled": True})
    elif backend == "bge_v2_m3":
        runtime["status"] = "ok" if runtime.get("model_loaded") else "degraded"
        if not runtime.get("model_loaded"):
            runtime["error"] = "reranker model is configured but not loaded"
    else:
        runtime.update({
            "status": "down",
            "error": f"unsupported reranker backend: {backend}",
        })
    return runtime


def _health_components() -> dict[str, dict[str, object]]:
    """Collect every dependency used by the RAG request path."""
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
    """Render friendly visual health check page."""
    data = health()
    return templates.TemplateResponse(
        request,
        "health.html",
        {"request": request, "health_data": data, "active_page": "health"}
    )


@app.get("/api/v1/health")
def health():
    """Extended health check with validation status."""
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
        "mineru_available": mineru_available(),
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
    """Root-level health endpoint."""
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


# ============================================
# Project Endpoints
# ============================================

@app.get("/api/v1/projects")
def api_projects():
    """List all projects."""
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
    """Create a new project."""
    with get_db() as db:
        if db.scalar(select(Project).where(Project.project_code == body.project_code)):
            raise HTTPException(409, "project_code already exists")
        p = Project(**body.model_dump(), created_at=now(), updated_at=now())
        db.add(p)
        db.commit()
        db.refresh(p)
        logger.info(f"Created project: {p.project_code}")
        return {"id": p.id, "project_code": p.project_code, "name": p.name}


@app.post("/api/v1/projects/sync")
def api_sync_project(body: ProjectSync):
    """Sync project from external system (idempotent)."""
    with get_db() as db:
        p = None
        if body.external_system and body.external_project_id:
            p = db.scalar(
                select(Project).where(
                    Project.external_system == body.external_system,
                    Project.external_project_id == body.external_project_id
                )
            )
        if not p:
            p = db.scalar(select(Project).where(Project.project_code == body.project_code))
        if not p:
            p = Project(**body.model_dump(), created_at=now(), updated_at=now())
            db.add(p)
            logger.info(f"Synced new project: {body.project_code}")
        else:
            for k, v in body.model_dump().items():
                setattr(p, k, v)
            p.updated_at = now()
            logger.info(f"Updated project: {p.project_code}")
        db.commit()
        db.refresh(p)
        return {"id": p.id, "project_code": p.project_code, "name": p.name, "status": p.status}


@app.get("/api/v1/projects/{project_id}")
def api_project(project_id: int, page: int = Query(1, ge=1), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)):
    """Get project details with paginated documents."""
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")
        total = db.scalar(select(func.count(Document.id)).where(Document.project_id == project_id)) or 0
        offset = (page - 1) * page_size
        docs = db.execute(
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.id.desc())
            .offset(offset)
            .limit(page_size)
        ).scalars().all()
        total_pages = (total + page_size - 1) // page_size
        return {
            "id": p.id, "project_code": p.project_code, "name": p.name, "status": p.status,
            "pagination": {"page": page, "page_size": page_size, "total_items": total, "total_pages": total_pages, "has_next": page < total_pages, "has_prev": page > 1},
            "documents": [{"id": d.id, "document_code": d.document_code, "filename": d.filename, "parse_status": d.parse_status, "document_type": d.document_type, "business_category": d.business_category} for d in docs]
        }


@app.get("/api/v1/projects/{project_id}/audit")
def api_project_audit(project_id: int):
    """Project audit with coverage analysis and recommendations."""
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")
        docs = db.execute(select(Document).where(Document.project_id == project_id)).scalars().all()
        counts = {"total": len(docs), "indexed": 0, "duplicates": 0, "queued": 0, "waiting_mineru": 0, "parse_failed": 0, "unclassified": 0, "missing_business_category": 0}
        types, cats, tax_cats = set(), set(), set()
        for d in docs:
            types.add(d.document_type)
            cats.add(d.business_category)
            if d.tax_category:
                tax_cats.add(d.tax_category)
            if d.parse_status == "INDEXED":
                counts["indexed"] += 1
            if d.duplicate_of_id:
                counts["duplicates"] += 1
            if d.parse_status in ("QUEUED", "UPLOADED"):
                counts["queued"] += 1
            if d.parse_status == "WAITING_MINERU":
                counts["waiting_mineru"] += 1
            if d.parse_status == "PARSE_FAILED":
                counts["parse_failed"] += 1
            if not d.document_type or d.document_type == "other":
                counts["unclassified"] += 1
            if not d.business_category:
                counts["missing_business_category"] += 1
        issues, recommendations = [], []
        for key, msg in [("waiting_mineru", "存在等待异步解析的资料"), ("parse_failed", "存在解析失败资料"), ("unclassified", "存在未分类资料，需要确认元数据"), ("duplicates", "存在重复文件，系统已阻止重复索引")]:
            if counts[key]:
                issues.append({"type": key.upper(), "count": counts[key], "message": msg})
        if "main_contract" not in types:
            issues.append({"type": "MISSING_MAIN_CONTRACT", "count": 1, "message": "未识别到项目主合同"})
        coverage = {}
        for cat in ["material", "labor", "equipment", "subcontract", "tax"]:
            has_cat = cat in cats
            coverage[cat] = has_cat
            if not has_cat:
                issues.append({"type": "CATEGORY_GAP", "category": cat, "message": f"尚未识别到 {cat} 类资料；如项目存在该业务，请补充"})
                recommendations.append(f"建议检查项目中是否存在 {cat} 类业务资料")
        coverage["tax_detail"] = sorted(tax_cats)
        coverage["missing_tax"] = len([t for t in ["vat", "enterprise_income", "individual_income"] if t not in tax_cats])
        if counts["unclassified"] > 0:
            recommendations.append("建议批量审核未分类文档，确保元数据准确")
        if counts["parse_failed"] > 0:
            recommendations.append("检查解析失败的文件，可能是格式问题或文件损坏")
        if counts["queued"] > counts["indexed"]:
            recommendations.append("仍有文档在队列中等待处理，请确认Worker正在运行")
        return {"project_id": project_id, "project_code": p.project_code, "counts": counts, "coverage": coverage, "issues": issues, "recommendations": recommendations}


# ============================================
# Document Endpoints
# ============================================

@app.post("/api/v1/documents/upload")
async def api_upload_document(project_id: int = Form(...), file: UploadFile = File(...), document_type: str = Form(""), entity_code: str = Form(""), counterparty_code: str = Form(""), business_category: str = Form(""), contract_no: str = Form(""), period: str = Form(""), auto_parse: bool = Form(True)):
    """Upload a document with metadata."""
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")
        try:
            data = await read_upload_limited(file)
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from exc
        d, jid = register_bytes(db, p, file.filename or "upload", data, {"document_type": document_type, "entity_code": entity_code, "counterparty_code": counterparty_code, "business_category": business_category, "contract_no": contract_no, "period": period}, auto_parse)
        db.refresh(d)
        return {"id": d.id, "document_code": d.document_code, "filename": d.filename, "parse_status": d.parse_status, "parse_message": d.parse_message, "duplicate_of_id": d.duplicate_of_id, "job_id": jid}



class FsListRequest(BaseModel):
    path: str

@app.post("/api/v1/fs/list-dirs")
def api_fs_list_dirs(
    body: FsListRequest,
    principal=Depends(require_web_role("admin", "operator")),
    _csrf=Depends(require_same_origin)
):
    """List directories for a given path (admin/operator only)."""
    import os
    target_path = body.path
    if not target_path:
        target_path = "/Volumes" if os.path.exists("/Volumes") else "/"
    elif not os.path.exists(target_path) or not os.path.isdir(target_path):
        return {"error": "路径不存在或不是文件夹"}
    
    dirs = []
    try:
        for entry in os.scandir(target_path):
            if entry.is_dir() and not entry.name.startswith("."):
                dirs.append({"name": entry.name, "path": entry.path})
    except Exception as e:
        return {"error": str(e)}
        
    parent_path = os.path.dirname(target_path) if target_path != "/" else "/"
    return {
        "current": target_path,
        "parent": parent_path,
        "dirs": sorted(dirs, key=lambda x: x["name"].lower())
    }

@app.post("/api/v1/documents/import-folder")

def api_import_folder(body: FolderImportRequest):
    """Import all supported files from a folder."""
    with get_db() as db:
        pid = _resolve_project(db, body.project_id, body.project_code)
        p = db.get(Project, pid)
        try:
            rows = scan_folder(db, p, body.path, body.recursive, body.auto_parse)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"project_id": pid, "path": body.path, "files": rows, "count": len(rows), "imported": sum(1 for r in rows if "error" not in r), "failed": sum(1 for r in rows if "error" in r)}


@app.get("/api/v1/documents/{document_id}")
def api_document(document_id: int):
    """Get document details."""
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
        for k, v in body.model_dump(exclude_none=True).items():
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
    """Re-parse a document."""
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


@app.delete("/api/v1/documents/{document_id}")
def api_delete_document(request: Request, document_id: int):
    """Delete a document, its chunks/jobs, and its stored files atomically.

    Files are first moved into a durable same-filesystem staging directory.
    The row/chunk/job deletion then commits as one DB transaction.  A failed
    DB transaction restores the staged files; only after a successful commit
    are staged files permanently removed.  Cleanup failures return a
    non-success response and leave a manifest for recovery.
    """
    storage_transaction = None
    with get_db() as db:
        # Lock the row while storage is staged so a worker cannot re-index the
        # same document concurrently with this destructive operation.
        d = db.execute(
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
        ).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "document not found")
        original_path = d.original_path
        parsed_dir = d.parsed_dir
        document_code = d.document_code

        try:
            storage_transaction = stage_document_cleanup(original_path, parsed_dir)
        except Exception as exc:
            db.rollback()
            security_audit(
                request,
                "document_delete",
                "storage_stage_failed",
                document_id=document_id,
                subject=document_code,
            )
            logger.exception("Could not stage storage for document %s", document_id)
            raise HTTPException(
                status_code=503,
                detail={
                    "deleted": False,
                    "database_deleted": False,
                    "cleanup_status": "not_started",
                    "document_id": document_id,
                },
            ) from exc

        try:
            # Chunks carry the pgvector values, so deleting them in the same
            # transaction removes the vector index rows without leaving
            # orphan chunks.  Ingest jobs have a document FK as well and must
            # be removed before the parent row.
            db.execute(sa_delete(Chunk).where(Chunk.document_id == document_id))
            db.execute(sa_delete(IngestJob).where(IngestJob.document_id == document_id))
            db.delete(d)
            db.commit()
        except Exception as exc:
            db.rollback()
            try:
                # Verify the commit outcome before compensating.  A dropped
                # connection can report an error after PostgreSQL committed;
                # in that case restoring files would create an orphan.
                database_deleted = (
                    db.scalar(select(Document.id).where(Document.id == document_id)) is None
                )
            except Exception as verify_exc:
                logger.critical(
                    "Document %s DB outcome is unknown after delete failure: %s",
                    document_id,
                    verify_exc,
                )
                security_audit(
                    request,
                    "document_delete",
                    "database_status_unknown",
                    document_id=document_id,
                    subject=document_code,
                )
                raise HTTPException(
                    status_code=503,
                    detail={
                        "deleted": False,
                        "database_deleted": None,
                        "cleanup_status": "recovery_required",
                        "document_id": document_id,
                    },
                ) from exc

            if database_deleted:
                # The commit may have succeeded even though the client saw a
                # connection error.  The DB is authoritative; finalize and
                # report any remaining file cleanup as pending.
                try:
                    finalize_document_cleanup(storage_transaction)
                except Exception as cleanup_exc:
                    logger.exception(
                        "Document %s was deleted in DB but file cleanup is pending",
                        document_id,
                    )
                    security_audit(
                        request,
                        "document_delete",
                        "cleanup_pending",
                        document_id=document_id,
                        subject=document_code,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "deleted": False,
                            "database_deleted": True,
                            "cleanup_status": "pending",
                            "document_id": document_id,
                        },
                    ) from cleanup_exc
                security_audit(
                    request,
                    "document_delete",
                    "success",
                    document_id=document_id,
                    subject=document_code,
                )
                logger.info("Deleted document %s: %s", document_id, document_code)
                return {
                    "deleted": True,
                    "database_deleted": True,
                    "cleanup_status": "complete",
                    "document_id": document_id,
                }
            else:
                try:
                    restore_document_cleanup(storage_transaction)
                except Exception as restore_exc:
                    logger.critical(
                        "Document %s DB delete rolled back but file recovery failed: %s",
                        document_id,
                        restore_exc,
                    )
                    security_audit(
                        request,
                        "document_delete",
                        "recovery_required",
                        document_id=document_id,
                        subject=document_code,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "deleted": False,
                            "database_deleted": False,
                            "cleanup_status": "recovery_required",
                            "document_id": document_id,
                        },
                    ) from restore_exc

            security_audit(
                request,
                "document_delete",
                "database_failed",
                document_id=document_id,
                subject=document_code,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "deleted": False,
                    "database_deleted": database_deleted,
                    "cleanup_status": "rolled_back" if not database_deleted else "pending",
                    "document_id": document_id,
                },
            ) from exc

    try:
        finalize_document_cleanup(storage_transaction)
    except Exception as exc:
        logger.exception("Document %s deleted in DB but file cleanup is pending", document_id)
        security_audit(
            request,
            "document_delete",
            "cleanup_pending",
            document_id=document_id,
            subject=document_code,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "deleted": False,
                "database_deleted": True,
                "cleanup_status": "pending",
                "document_id": document_id,
            },
        ) from exc

    security_audit(request, "document_delete", "success", document_id=document_id, subject=document_code)
    logger.info(f"Deleted document {document_id}: {document_code}")
    return {
        "deleted": True,
        "database_deleted": True,
        "cleanup_status": "complete",
        "document_id": document_id,
    }


@app.get("/api/v1/documents/{document_id}/original")
def api_original(document_id: int, principal=Depends(require_web_or_service_read)):
    """Download original document file."""
    from urllib.parse import quote
    with get_db() as db:
        d = db.get(Document, document_id)
        if not d:
            raise HTTPException(404, "file not found")
        try:
            path = validate_stored_file(d.original_path)
        except (StorageError, PathTraversalError) as exc:
            raise HTTPException(404, "file not found") from exc
        filename = Path(d.filename or "download").name
        filename = "".join(ch for ch in filename if ch not in "\r\n\"\\") or "download"
        encoded_fn = quote(filename)
        return FileResponse(
            path,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"}
        )


# ============================================
# Job Endpoints
# ============================================

_MONITORED_JOB_STATUS_KEYS = {
    "RUNNING": "active",
    "QUEUED": "queued",
    "RETRY": "retry",
    "FAILED": "failed",
}

_WAITING_DOCUMENT_STATUSES = {"QUEUED", "WAITING_MINERU", "PARSE_FAILED", "UPLOADED"}


def _non_negative_count(value, label: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _document_monitor_kpis(db) -> dict:
    """Build the dashboard KPI contract from document/chunk tables.

    ``waiting_documents`` intentionally counts document parse states rather
    than ingest jobs, while ``indexed_chunks`` counts the actual vectorized
    chunks displayed by the dashboard.
    """
    status_counts = {}
    rows = db.execute(
        select(Document.parse_status, func.count(Document.id)).group_by(Document.parse_status)
    ).all()
    for parse_status, count in rows:
        normalized_status = str(parse_status or "").strip().upper()
        if not normalized_status:
            continue
        parsed_count = _non_negative_count(count, f"document count for {normalized_status}")
        status_counts[normalized_status] = status_counts.get(normalized_status, 0) + parsed_count

    indexed_chunks = _non_negative_count(db.scalar(select(func.count(Chunk.id))), "indexed chunk count")
    return {
        "document_total": sum(status_counts.values()),
        "indexed_documents": status_counts.get("INDEXED", 0),
        "indexed_chunks": indexed_chunks,
        "waiting_documents": sum(status_counts.get(status, 0) for status in _WAITING_DOCUMENT_STATUSES),
    }


def _job_monitor_snapshot(db) -> dict:
    """Build the dashboard's job-monitor contract from the ingest queue.

    ``RUNNING`` is the active MinerU process.  Queued and retry jobs are
    still pending work, while failed jobs remain visible as an error state so
    the dashboard cannot incorrectly report an idle worker.
    """
    counts = {status: 0 for status in _MONITORED_JOB_STATUS_KEYS.values()}
    rows = db.execute(
        select(IngestJob.status, func.count(IngestJob.id)).group_by(IngestJob.status)
    ).all()
    for status, count in rows:
        key = _MONITORED_JOB_STATUS_KEYS.get(str(status or "").upper())
        if key is not None:
            counts[key] = int(count or 0)

    active_job = db.scalar(
        select(IngestJob)
        .where(IngestJob.status == "RUNNING")
        .order_by(IngestJob.id.asc())
        .limit(1)
    )
    active = active_job is not None
    if active:
        status = "active"
    elif counts["queued"]:
        status = "queued"
    elif counts["retry"]:
        status = "retry"
    elif counts["failed"]:
        status = "failed"
    else:
        status = "idle"

    payload = {
        "active": active,
        "status": status,
        "counts": counts,
        # Keep flat counters for simple consumers while the nested ``counts``
        # object gives the frontend one stable contract to validate.
        "active_count": counts["active"],
        "queued_count": counts["queued"],
        "retry_count": counts["retry"],
        "failed_count": counts["failed"],
    }
    payload.update(_document_monitor_kpis(db))
    proj_rows = db.execute(
        select(
            Document.project_id,
            func.count(Document.id),
            func.count(case((Document.parse_status == "INDEXED", 1))),
        ).group_by(Document.project_id)
    ).all()
    payload["projects_stats"] = {
        int(row[0]): {"doc_count": int(row[1] or 0), "indexed_count": int(row[2] or 0)}
        for row in proj_rows
        if row[0] is not None
    }
    if active_job is None:
        payload["active_job"] = None
        return payload

    doc = db.get(Document, active_job.document_id)
    active_view = {
        "id": active_job.id,
        "document_id": active_job.document_id,
        "document_name": doc.filename if doc else "未知文件",
        "attempts": active_job.attempts,
        "started_at": active_job.started_at,
    }
    payload["active_job"] = active_view
    # Preserve the original top-level fields consumed by older dashboard
    # builds and external read-only callers.
    payload.update(
        {
            "job_id": active_view["id"],
            "document_name": active_view["document_name"],
            "attempts": active_view["attempts"],
            "started_at": active_view["started_at"],
        }
    )
    return payload


@app.get("/api/v1/jobs/active")
def get_active_job(principal=Depends(require_web_or_service_read)):
    """Return the reliable MinerU dashboard monitoring snapshot."""
    del principal
    with get_db() as db:
        return _job_monitor_snapshot(db)


@app.get("/api/v1/jobs/{job_id}")
def api_job(job_id: int):
    """Get job details."""
    with get_db() as db:
        j = db.get(IngestJob, job_id)
        if not j:
            raise HTTPException(404, "job not found")
        return {"id": j.id, "document_id": j.document_id, "job_type": j.job_type, "status": j.status, "attempts": j.attempts, "max_attempts": j.max_attempts, "message": j.message, "last_error": j.last_error, "created_at": j.created_at, "started_at": j.started_at, "finished_at": j.finished_at, "next_retry_at": j.next_retry_at}


@app.post("/api/v1/jobs/process-next")
def api_process_next():
    """Process next job in queue."""
    return {"processed": process_next()}


@app.get("/api/v1/jobs")
def api_jobs(status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    """List jobs with optional status filter and pagination."""
    with get_db() as db:
        stmt = select(IngestJob).order_by(IngestJob.id.desc())
        if status:
            stmt = stmt.where(IngestJob.status == status)
        total = db.scalar(select(func.count(IngestJob.id))) if not status else db.scalar(select(func.count(IngestJob.id)).where(IngestJob.status == status))
        offset = (page - 1) * page_size
        jobs = db.execute(stmt.offset(offset).limit(page_size)).scalars().all()
        total_pages = (total + page_size - 1) // page_size if total else 0
        return {"pagination": {"page": page, "page_size": page_size, "total_items": total or 0, "total_pages": total_pages, "has_next": page < total_pages, "has_prev": page > 1}, "items": [{"id": j.id, "document_id": j.document_id, "job_type": j.job_type, "status": j.status, "attempts": j.attempts, "message": j.message, "created_at": j.created_at, "finished_at": j.finished_at} for j in jobs]}


# ============================================
# Entity Endpoints
# ============================================

@app.get("/api/v1/entities")
def api_entities(q: str = Query("", description="Search by name"), industry: str = Query("", description="Filter by industry"), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    """List all entities with optional search and filter."""
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
    """Get summary of all entities by industry."""
    with get_db() as db:
        rows = db.execute(select(Entity.industry, func.count(Entity.id)).group_by(Entity.industry).order_by(Entity.industry)).all()
        total = db.scalar(select(func.count(Entity.id))) or 0
        return {"total": total, "by_industry": [{"industry": r[0] or "未分类", "count": r[1]} for r in rows]}


# ============================================
# Retrieval Endpoints
# ============================================

@app.get("/api/v1/entities/{entity_id}")
def api_entity(entity_id: int):
    """Get entity details."""
    with get_db() as db:
        e = db.get(Entity, entity_id)
        if not e:
            raise HTTPException(404, "entity not found")
        return {"id": e.id, "name": e.name, "short_name": e.short_name, "entity_type": e.entity_type, "industry": e.industry, "legal_representative": e.legal_representative, "legal_rep_id": e.legal_rep_id, "legal_rep_phone": e.legal_rep_phone, "shareholders": e.shareholders, "supervisor": e.supervisor, "finance_officer": e.finance_officer, "registered_capital": e.registered_capital, "establishment_date": e.establishment_date, "acquisition_date": e.acquisition_date, "registration_authority": e.registration_authority, "registration_number": e.registration_number, "unified_social_credit_code": e.unified_social_credit_code, "business_scope": e.business_scope, "contributed_legal": e.contributed_legal, "contributed_shareholder": e.contributed_shareholder, "note": e.note, "source": e.source, "data_as_of": e.data_as_of, "created_at": e.created_at, "updated_at": e.updated_at}


@app.post("/api/v1/entities")
def api_create_entity(body: EntityCreate):
    """Create a new entity."""
    with get_db() as db:
        if db.scalar(select(Entity).where(Entity.entity_code == body.entity_code)):
            raise HTTPException(409, "entity_code already exists")
        if db.scalar(select(Entity).where(Entity.name == body.name)):
            raise HTTPException(409, "entity with this name already exists")
        e = Entity(**body.model_dump(), created_at=now(), updated_at=now())
        db.add(e)
        db.commit()
        db.refresh(e)
        logger.info(f"Created entity: {e.name}")
        return {"id": e.id, "name": e.name, "industry": e.industry}


@app.patch("/api/v1/entities/{entity_id}")
def api_patch_entity(entity_id: int, body: EntityPatch):
    """Update entity details."""
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
        logger.info(f"Updated entity {entity_id}: {e.name}")
        return {"id": e.id, "name": e.name, "industry": e.industry}


@app.post("/api/v1/entities/batch")
def api_batch_create_entities(body: list[EntityCreate]):
    """Upsert canonical internal entities by immutable entity_code."""
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
        logger.info("Batch upserted %s canonical internal entities", len(results))
        return {"total": len(results), "items": results}


@app.post("/api/v1/entities/import-file")
async def api_import_entities_file(
    file: UploadFile = File(...),
    principal=Depends(require_web_or_service_role("admin")),
):
    """Import and auto-recognize internal entities from Excel, Word, or PDF file."""
    del principal
    from .services.entity_importer import import_entities_from_file_bytes

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件内容为空")

    with get_db() as db:
        try:
            result = import_entities_from_file_bytes(db, content, file.filename or "file.xlsx")
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("Failed to parse and import entities from file %s", file.filename)
            raise HTTPException(status_code=500, detail=f"文件解析失败: {exc}")


@app.get("/api/v1/external-parties")
def api_external_parties(q: str = ""):
    """List system-external owners/suppliers/labor/equipment counterparties."""
    with get_db() as db:
        stmt = select(ExternalParty).order_by(ExternalParty.code)
        if q:
            stmt = stmt.where(ExternalParty.name.ilike(f"%{q}%") | ExternalParty.short_name.ilike(f"%{q}%"))
        rows = db.execute(stmt).scalars().all()
        return [{"id": x.id, "code": x.code, "name": x.name, "short_name": x.short_name, "kind": x.kind, "tax_id": x.tax_id, "active": x.active} for x in rows]


@app.post("/api/v1/external-parties")
def api_create_external_party(body: ExternalPartyCreate):
    """Create an external counterparty without polluting the 26-unit internal master."""
    with get_db() as db:
        if db.scalar(select(ExternalParty).where(ExternalParty.code == body.code)):
            raise HTTPException(409, "external party code already exists")
        party = ExternalParty(**body.model_dump())
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


@app.post("/api/v1/retrieve")
def api_retrieve(body: RetrieveRequest):
    """Retrieve relevant document chunks."""
    with get_db() as db:
        pid = _resolve_project(db, body.project_id, body.project_code)
        out = retrieve(db, pid, body.query, body.filters, body.top_k, body.rerank)
        return {"project_id": pid, "query": body.query, "results": out}


@app.post("/api/v1/query")
def api_query(body: QueryRequest):
    """Retrieve and optionally generate answer."""
    with get_db() as db:
        pid = _resolve_project(db, body.project_id, body.project_code)
        evidence = retrieve(db, pid, body.query, body.filters, body.top_k, body.rerank)
    answer = ""
    if body.answer:
        answer = answer_with_llm(body.query, evidence)
    return {"project_id": pid, "query": body.query, "answer": answer, "citations": [{"index": i + 1, "document_id": x["document_id"], "filename": x["filename"], "page_start": x["page_start"], "page_end": x["page_end"], "heading_path": x["heading_path"], "chunk_id": x["chunk_id"]} for i, x in enumerate(evidence)], "results": evidence}


@app.get("/api/v1/stats")
def api_stats(project_id: Optional[int] = None):
    """Get query statistics."""
    with get_db() as db:
        return get_query_stats(db, project_id)


# ============================================
# Tax Extraction Endpoints
# ============================================

@app.post("/api/v1/extract-tax", response_model=ExtractTaxResponse)
def api_extract_tax(body: ExtractTaxRequest):
    """Extract structured tax data from project documents."""
    with get_db() as db:
        pid = _resolve_project(db, body.project_id, body.project_code)
        filters = {}
        if body.period_start:
            filters["period"] = body.period_start
        if body.entity_code:
            filters["entity_code"] = body.entity_code
        if body.counterparty_code:
            filters["counterparty_code"] = body.counterparty_code
        doc_types = EXTRACT_DOC_TYPE_FILTERS.get(body.extract_type, [])
        if doc_types:
            filters["document_type"] = doc_types
        query = EXTRACT_QUERY_TEMPLATES.get(body.extract_type, body.extract_type)
        raw_chunks = retrieve(db, pid, query, filters, body.top_k, use_rerank=False)

        # Deduplicate candidate chunks by document_id to avoid extracting redundant copies of the same document
        seen_doc_ids = set()
        chunks = []
        for ch in raw_chunks:
            doc_id = ch.get("document_id")
            if doc_id not in seen_doc_ids:
                seen_doc_ids.add(doc_id)
                chunks.append(ch)

        def extract_one(chunk: dict) -> tuple[ExtractedItem | None, str | None]:
            chunk_text = chunk.get("text", "")
            if not chunk_text or len(chunk_text.strip()) < 15:
                return None, f"chunk {chunk['chunk_id']}: text too short, skipped"
            try:
                fields, confidence = extract_from_chunk(chunk_text, body.extract_type)
                return ExtractedItem(
                    source_chunk_id=chunk["chunk_id"],
                    source_document_id=chunk["document_id"],
                    filename=chunk["filename"],
                    page_start=chunk.get("page_start"),
                    page_end=chunk.get("page_end"),
                    confidence=confidence,
                    extract_type=body.extract_type,
                    fields=fields,
                ), None
            except ExtractionError as e:
                return None, f"chunk {chunk['chunk_id']}: {e}"

        workers = min(_tax_extract_workers(), max(1, len(chunks)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tax-extract") as executor:
            outcomes = list(executor.map(extract_one, chunks))
        extracted_items = [item for item, error in outcomes if item is not None]
        errors = [error for item, error in outcomes if item is None and error]
    return ExtractTaxResponse(project_id=pid, extract_type=body.extract_type, query_used=query, total_chunks=len(chunks), total_extracted=len(extracted_items), extracted_items=extracted_items, errors=errors, llm_available=llm_extraction_available())


# ============================================
# Helper Functions
# ============================================

def _resolve_project(db, project_id, project_code):
    """Resolve project ID from ID or code."""
    p = db.get(Project, project_id) if project_id else db.scalar(select(Project).where(Project.project_code == project_code)) if project_code else None
    if not p:
        raise HTTPException(404, "project not found")
    return p.id


# ============================================
# Web UI Endpoints
# ============================================


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, principal=Depends(require_web_auth)):
    """Dashboard page."""
    with get_db() as db:
        projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
        canonical_entities = _canonical_entity_views(db)
        monitor_kpis = _document_monitor_kpis(db)
        # 查询每个项目的凭证数与涉及的往来主体（包含我方主体与对手方主体）
        project_stats = {}
        for p in projects:
            p_docs = db.scalar(select(func.count(Document.id)).where(Document.project_id == p.id)) or 0
            p_indexed = db.scalar(select(func.count(Document.id)).where(Document.project_id == p.id, Document.parse_status == "INDEXED")) or 0
            
            ents_set = set()
            if p.entity_code:
                ents_set.add(p.entity_code.strip())
                
            e1 = db.execute(
                select(Document.entity_code).where(
                    Document.project_id == p.id,
                    Document.entity_code.is_not(None),
                    Document.entity_code != ""
                ).distinct()
            ).scalars().all()
            for code in e1:
                if code:
                    ents_set.add(code.strip())
                    
            e2 = db.execute(
                select(Document.counterparty_code).where(
                    Document.project_id == p.id,
                    Document.counterparty_code.is_not(None),
                    Document.counterparty_code != ""
                ).distinct()
            ).scalars().all()
            for code in e2:
                if code:
                    ents_set.add(code.strip())
                    
            all_ents = sorted(list(ents_set))
            internal_ents = [c for c in all_ents if not c.startswith("EXT-")]
            external_ents = [c for c in all_ents if c.startswith("EXT-")]
            
            project_stats[p.id] = {
                "doc_count": p_docs,
                "indexed_count": p_indexed,
                "entity_count": len(all_ents),
                "entities": all_ents,
                "internal_count": len(internal_ents),
                "external_count": len(external_ents),
                "internal_entities": internal_ents,
                "external_entities": external_ents,
            }

        return templates.TemplateResponse(request, "dashboard.html", {
            "projects": projects,
            "project_stats": project_stats,
            "canonical_entities": canonical_entities,
            "entity_by_code": {x["entity_code"]: x for x in canonical_entities},
            "entity_summary": _entity_summary(canonical_entities),
            "docs": monitor_kpis["document_total"],
            "indexed_documents": monitor_kpis["indexed_documents"],
            "indexed_chunks": monitor_kpis["indexed_chunks"],
            "waiting": monitor_kpis["waiting_documents"],
            "mineru": mineru_available(),
            "postgres": IS_POSTGRES,
            "worker": get_worker_status(),
        })


@app.post("/projects")
def web_create_project(
    project_code: str = Form(...),
    name: str = Form(...),
    external_system: str = Form(""),
    external_project_id: str = Form(""),
    principal=Depends(require_web_role("admin", "operator")),
    _csrf=Depends(require_same_origin),
):
    """Web: Create project."""
    with get_db() as db:
        existing = db.scalar(select(Project).where(Project.project_code == project_code))
        if not existing:
            db.add(Project(project_code=project_code, name=name, external_system=external_system, external_project_id=external_project_id, created_at=now(), updated_at=now()))
            db.commit()
    return RedirectResponse("/", 303)


@app.get("/projects/{project_ref}", response_class=HTMLResponse)
def web_project(request: Request, project_ref: str, principal=Depends(require_web_auth)):
    """Web: Project detail page (supports numeric id or project_code)."""
    with get_db() as db:
        if project_ref.isdigit():
            p = db.get(Project, int(project_ref))
        else:
            p = db.scalar(select(Project).where(Project.project_code == project_ref))
        if not p:
            return HTMLResponse("project not found", 404)
        project_id = p.id
        docs = db.execute(select(Document).where(Document.project_id == project_id).order_by(Document.id.desc()).limit(100)).scalars().all()
        jobs = db.execute(select(IngestJob).join(Document, IngestJob.document_id == Document.id).where(Document.project_id == project_id).order_by(IngestJob.id.desc()).limit(30)).scalars().all()
        canonical_entities = _canonical_entity_views(db)
        entity_by_code = {x["entity_code"]: x for x in canonical_entities}
        return templates.TemplateResponse(request, "project.html", {"project": p, "canonical_entities": canonical_entities, "entity_by_code": entity_by_code, "documents": docs, "jobs": jobs, "active_page": "projects", "mineru": mineru_available(), "postgres": IS_POSTGRES})


@app.post("/projects/{project_id}/upload")
async def web_upload(
    project_id: int,
    file: UploadFile = File(...),
    document_type: str = Form(""),
    entity_code: str = Form(""),
    counterparty_code: str = Form(""),
    business_category: str = Form(""),
    period: str = Form(""),
    principal=Depends(require_web_role("admin", "operator")),
    _csrf=Depends(require_same_origin),
):
    """Web: Upload document."""
    try:
        data = await read_upload_limited(file)
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")
        try:
            register_bytes(db, p, file.filename or "upload", data, {"document_type": document_type, "entity_code": entity_code, "counterparty_code": counterparty_code, "business_category": business_category, "contract_no": "", "period": period}, True)
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            raise HTTPException(500, f"Upload failed: {e}")
    return RedirectResponse(f"/projects/{project_id}", 303)


@app.post("/projects/{project_id}/import-folder")
def web_import_folder(
    project_id: int,
    path: str = Form(...),
    recursive: bool = Form(False),
    principal=Depends(require_web_role("admin", "operator")),
    _csrf=Depends(require_same_origin),
):
    """Web: Import folder."""
    api_import_folder(FolderImportRequest(project_id=project_id, path=path, recursive=recursive, auto_parse=True))
    return RedirectResponse(f"/projects/{project_id}", 303)


@app.get("/documents/{document_id}", response_class=HTMLResponse)
def web_document(request: Request, document_id: int, principal=Depends(require_web_auth)):
    """Web: Document detail page."""
    with get_db() as db:
        d = db.get(Document, document_id)
        if not d:
            return HTMLResponse("document not found", 404)
        chunks = db.execute(select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index).limit(50)).scalars().all()
        p = db.get(Project, d.project_id)
        jobs = db.execute(select(IngestJob).where(IngestJob.document_id == document_id).order_by(IngestJob.id.desc()).limit(20)).scalars().all()
        canonical_entities = _canonical_entity_views(db)
        entity_by_code = {x["entity_code"]: x for x in canonical_entities}
        return templates.TemplateResponse(request, "document.html", {"doc": d, "project": p, "chunks": chunks, "jobs": jobs, "canonical_entities": canonical_entities, "entity_by_code": entity_by_code, "active_page": "projects", "mineru": mineru_available(), "postgres": IS_POSTGRES})


@app.post("/documents/{document_id}/parse")
def web_parse(
    document_id: int,
    principal=Depends(require_web_role("admin", "operator")),
    _csrf=Depends(require_same_origin),
):
    """Web: Re-parse document."""
    api_parse(document_id)
    return RedirectResponse(f"/documents/{document_id}", 303)


@app.get("/search", response_class=HTMLResponse)
def web_search(
    request: Request,
    project_id: Optional[int] = None,
    q: str = "",
    business_category: str = "",
    entity_code: str = "",
    business_role: str = "",
    principal=Depends(require_web_auth),
):
    """Web: Search page."""
    with get_db() as db:
        projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
        canonical_entities = _canonical_entity_views(db)
        entity_by_code = {x["entity_code"]: x for x in canonical_entities}
        results = []
        entity_filter_error = ""
        requested_entity_code = (entity_code or "").strip().upper()
        if requested_entity_code and is_canonical_entity_code(requested_entity_code):
            entity_code = requested_entity_code
        elif requested_entity_code:
            legacy_role = _business_role_key(requested_entity_code)
            if legacy_role in BUSINESS_ROLE_META:
                business_role = legacy_role
                entity_code = ""
            else:
                entity_code = ""
                entity_filter_error = "未识别的实际单位代码，已忽略该过滤条件"
        if business_role:
            business_role = _business_role_key(business_role)
            if business_role not in BUSINESS_ROLE_META:
                business_role = ""
        if project_id and q:
            filters = {}
            if business_category:
                filters["business_category"] = business_category
            if entity_code:
                filters["entity_code"] = entity_code
            elif business_role:
                filters["entity_code"] = [x["entity_code"] for x in canonical_entities if x["business_role"] == business_role] or ["__no_canonical_entity__"]
            results = retrieve(db, project_id, q, filters, 12, True)
        return templates.TemplateResponse(request, "search.html", {"projects": projects, "project_id": project_id, "q": q, "business_category": business_category, "entity_code": entity_code, "business_role": business_role, "entity_filter_error": entity_filter_error, "canonical_entities": canonical_entities, "entity_by_code": entity_by_code, "results": results, "active_page": "search", "mineru": mineru_available(), "postgres": IS_POSTGRES})


@app.get("/regulations", response_class=HTMLResponse)
def web_regulations(
    request: Request,
    q: str = "",
    entity: str = "",
    business_role: str = "",
    jurisdiction: str = "",
    level: str = "",
    status: str = "",
    principal=Depends(require_web_auth),
):
    """Web: Regulations browser page."""
    category_aliases = {"entity_a": "construction", "entity_b": "trade", "entity_c": "labor", "entity_d": "equipment", "a": "construction", "b": "trade", "c": "labor", "d": "equipment", "construction": "construction", "trade": "trade", "labor": "labor", "equipment": "equipment"}
    requested_role = (business_role or entity or "").strip().lower()
    role_filter = category_aliases.get(requested_role, "")
    category_meta = {"construction": {"order": 1, "label": "建筑施工业务角色", "badge": "badge-cyan"}, "trade": {"order": 2, "label": "商贸物资业务角色", "badge": "badge-emerald"}, "labor": {"order": 3, "label": "建筑劳务业务角色", "badge": "badge-purple"}, "equipment": {"order": 4, "label": "工程设备业务角色", "badge": "badge-amber"}, "national_vat": {"order": 5, "label": "国家增值税", "badge": "badge-cyan"}, "national_other": {"order": 6, "label": "国家其他税", "badge": "badge-cyan"}, "sichuan": {"order": 7, "label": "四川省规定", "badge": "badge-emerald"}, "chengdu": {"order": 8, "label": "成都市公告", "badge": "badge-amber"}}
    with get_db() as db:
        stmt = select(Regulation)
        if q:
            stmt = stmt.where((Regulation.title.ilike(f"%{q}%")) | (Regulation.document_no.ilike(f"%{q}%")) | (Regulation.full_text.ilike(f"%{q}%")))
        if level:
            stmt = stmt.where(Regulation.legal_level == level)
        if jurisdiction:
            stmt = stmt.where(Regulation.jurisdiction == jurisdiction)
        if status:
            stmt = stmt.where(Regulation.status.ilike(f"%{status}%"))
        raw_regs = db.execute(stmt).scalars().all()
        all_raw_regs = db.execute(select(Regulation)).scalars().all()
        counts = {"all": len(all_raw_regs), "construction": 0, "trade": 0, "labor": 0, "equipment": 0, "national": 0, "sichuan": 0, "chengdu": 0, "valid": 0, "partial": 0}
        for r in all_raw_regs:
            cat = "national_other"
            if r.metadata_json:
                try:
                    m = json.loads(r.metadata_json)
                    raw_category = m.get("business_role") or m.get("category", cat)
                    if "/" in str(raw_category):
                        raw_category = str(raw_category).split("/")[-1]
                    cat = category_aliases.get(str(raw_category).strip().lower(), raw_category)
                except Exception:
                    pass
            if cat in counts:
                counts[cat] += 1
            if r.jurisdiction == "全国":
                counts["national"] += 1
            elif r.jurisdiction == "四川省":
                counts["sichuan"] += 1
            elif r.jurisdiction == "成都市":
                counts["chengdu"] += 1
            if "部分" in (r.status or ""):
                counts["partial"] += 1
            else:
                counts["valid"] += 1

        enriched_regs = []
        for r in raw_regs:
            cat = "national_other"
            if r.metadata_json:
                try:
                    m = json.loads(r.metadata_json)
                    raw_category = m.get("business_role") or m.get("category", cat)
                    if "/" in str(raw_category):
                        raw_category = str(raw_category).split("/")[-1]
                    cat = category_aliases.get(str(raw_category).strip().lower(), raw_category)
                except Exception:
                    pass
            cinfo = category_meta.get(cat, {"order": 99, "label": "其他法规", "badge": "badge-muted"})
            if role_filter and cat != role_filter:
                continue
            enriched_regs.append({"id": r.id, "document_no": r.document_no, "title": r.title, "issuer": r.issuer or "-", "legal_level": r.legal_level or "规范性文件", "jurisdiction": r.jurisdiction or "全国", "tax_type": r.tax_type or "全部税种", "industry": r.industry or "建筑业", "publish_date": r.publish_date or "-", "effective_date": r.effective_date or "-", "status": r.status or "现行有效", "full_text": r.full_text or "", "category": cat, "category_order": cinfo["order"], "category_label": cinfo["label"], "category_badge": cinfo["badge"]})
        enriched_regs.sort(key=lambda x: (x["category_order"], x["id"]))
        return templates.TemplateResponse(request, "regulations.html", {"regulations": enriched_regs, "counts": counts, "q": q, "entity": role_filter, "business_role": role_filter, "jurisdiction": jurisdiction, "level": level, "status": status, "active_page": "regulations", "mineru": mineru_available(), "postgres": IS_POSTGRES})


@app.get("/entities", response_class=HTMLResponse)
def web_entities(request: Request, principal=Depends(require_web_auth)):
    """Web: canonical entity master page."""
    with get_db() as db:
        entities = _canonical_entity_views(db)
        return templates.TemplateResponse(request, "entities.html", {"entities": entities, "entity_summary": _entity_summary(entities), "active_page": "entities", "mineru": mineru_available(), "postgres": IS_POSTGRES})


@app.get("/entities/{entity_code}", response_class=HTMLResponse)
def web_entity_detail(request: Request, entity_code: str, principal=Depends(require_web_auth)):
    """Web: Entity detailed transaction and project breakdown page."""
    code = (entity_code or "").strip().upper()
    with get_db() as db:
        entity = db.scalar(select(Entity).where(Entity.entity_code == code))
        if not entity:
            ext = db.scalar(select(ExternalParty).where(ExternalParty.code == code))
            if not ext:
                raise HTTPException(404, f"未找到单位代码为 {code} 的主体或外部合作单位数据")
            entity = Entity(
                entity_code=ext.code,
                name=ext.name,
                short_name=ext.short_name,
                business_role=ext.kind,
                tax_id=ext.tax_id,
                legal_entity=True,
                source="系统外",
                note=f"系统外合作单位 ({ext.code})",
            )

        # Find all documents associated with this entity (as primary or counterparty)
        docs = db.execute(
            select(Document)
            .where(or_(Document.entity_code == code, Document.counterparty_code == code))
            .order_by(Document.id.desc())
        ).scalars().all()

        # Group by project and aggregate financials
        project_ids = list(set(d.project_id for d in docs if d.project_id))
        projects_dict = {}
        if project_ids:
            prjs = db.execute(select(Project).where(Project.id.in_(project_ids))).scalars().all()
            projects_dict = {p.id: p for p in prjs}

        # Calculate transaction metrics
        financial_summary = {
            "doc_count": len(docs),
            "project_count": len(project_ids),
            "vat_input": sum(d.tax_vat_input or 0.0 for d in docs),
            "vat_output": sum(d.tax_vat_output or 0.0 for d in docs),
            "vat_paid": sum(d.tax_vat_paid or 0.0 for d in docs),
            "income_tax_paid": sum(d.tax_income_paid or 0.0 for d in docs),
            "tax_total": sum(d.tax_total or 0.0 for d in docs),
        }

        # Build enriched document view with clickable links
        enriched_docs = []
        for d in docs:
            prj = projects_dict.get(d.project_id)
            enriched_docs.append({
                "id": d.id,
                "document_code": d.document_code,
                "filename": d.filename,
                "document_type": d.document_type,
                "project_id": d.project_id,
                "project_name": prj.name if prj else "未知项目",
                "project_code": prj.project_code if prj else "-",
                "counterparty_code": d.counterparty_code,
                "invoice_no": d.invoice_no,
                "contract_no": d.contract_no,
                "tax_vat_input": d.tax_vat_input or 0.0,
                "tax_vat_output": d.tax_vat_output or 0.0,
                "tax_vat_paid": d.tax_vat_paid or 0.0,
                "tax_total": d.tax_total or 0.0,
                "period": d.period,
                "parse_status": d.parse_status,
            })

        # Role info
        role = _business_role_key(entity.business_role)
        role_meta = BUSINESS_ROLE_META.get(role, {
            "label": "未分类业务",
            "badge": "badge-muted",
            "color": "#94a3b8",
            "description": "待补充业务角色",
        })

        canonical_entities = _canonical_entity_views(db)
        entity_by_code = {x["entity_code"]: x for x in canonical_entities}

        return templates.TemplateResponse(
            request,
            "entity_detail.html",
            {
                "request": request,
                "entity": entity,
                "role_meta": role_meta,
                "summary": financial_summary,
                "projects": list(projects_dict.values()),
                "documents": enriched_docs,
                "canonical_entities": canonical_entities,
                "entity_by_code": entity_by_code,
                "active_page": "entities",
                "mineru": mineru_available(),
                "postgres": IS_POSTGRES,
            }
        )


@app.get("/projects/{project_id}/audit", response_class=HTMLResponse)
def web_audit(request: Request, project_id: int, principal=Depends(require_web_auth)):
    """Web: Project audit page."""
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            return HTMLResponse("project not found", 404)
        docs = db.execute(select(Document).where(Document.project_id == project_id).order_by(Document.id.desc())).scalars().all()
        duplicates = [d for d in docs if d.duplicate_of_id]
        indexed_cnt = sum(1 for d in docs if d.parse_status == "INDEXED")
        return templates.TemplateResponse(request, "audit.html", {"project": p, "documents": docs, "duplicates": duplicates, "indexed_cnt": indexed_cnt, "active_page": "projects", "mineru": mineru_available(), "postgres": IS_POSTGRES})


# ==================== V1.1: 法规知识引擎 API =========

@app.get("/api/v1/regulations")
def api_list_regulations(jurisdiction: str | None = None, tax_type: str | None = None, status: str = "effective", legal_level: str | None = None, limit: int = 100, offset: int = 0):
    """列出法规，支持 Metadata 过滤"""
    db = SessionLocal()
    conditions = []
    if jurisdiction:
        conditions.append(Regulation.jurisdiction.in_([jurisdiction, "全国"]))
    if tax_type:
        conditions.append(or_(Regulation.tax_type == "", Regulation.tax_type.contains(tax_type)))
    if legal_level:
        conditions.append(Regulation.legal_level == legal_level)
    if status:
        conditions.append(Regulation.status == status)
    stmt = select(Regulation).order_by(Regulation.publish_date.desc())
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.limit(limit).offset(offset)
    regs = db.execute(stmt).scalars().all()
    total = db.scalar(select(func.count(Regulation.id))) or 0
    db.close()
    return {"total": total, "offset": offset, "limit": limit, "regulations": [{"id": r.id, "title": r.title, "document_no": r.document_no, "issuer": r.issuer, "legal_level": r.legal_level, "jurisdiction": r.jurisdiction, "tax_type": r.tax_type, "industry": r.industry, "publish_date": r.publish_date, "effective_date": r.effective_date, "expiry_date": r.expiry_date, "status": r.status, "source": r.source} for r in regs]}


@app.post("/api/v1/regulations")
def api_create_regulation(body: RegulationCreate):
    """创建法规"""
    db = SessionLocal()
    existing = db.scalar(select(Regulation).where(Regulation.document_no == body.document_no))
    if existing:
        db.close()
        raise HTTPException(409, "该文号法规已存在")
    reg = Regulation(**body.model_dump(), metadata_json=json.dumps(body.metadata_json, ensure_ascii=False), search_text=f"{body.title} {body.document_no} {body.issuer} {body.tax_type}")
    db.add(reg)
    db.commit()
    db.refresh(reg)
    db.close()
    return {"id": reg.id, "title": reg.title, "document_no": reg.document_no, "status": "created"}


@app.get("/api/v1/regulations/{regulation_id}")
def api_get_regulation(regulation_id: int, principal=Depends(require_web_or_service_read)):
    """获取法规详情"""
    db = SessionLocal()
    r = db.get(Regulation, regulation_id)
    if not r:
        db.close()
        raise HTTPException(404, "法规不存在")
    article_count = db.scalar(select(func.count(RegulationArticle.id)).where(RegulationArticle.regulation_id == regulation_id)) or 0
    out = {"id": r.id, "title": r.title, "document_no": r.document_no, "issuer": r.issuer, "legal_level": r.legal_level, "jurisdiction": r.jurisdiction, "tax_type": r.tax_type, "industry": r.industry, "publish_date": r.publish_date, "effective_date": r.effective_date, "expiry_date": r.expiry_date, "status": r.status, "full_text": r.full_text, "source": r.source, "article_count": article_count}
    db.close()
    return out


@app.patch("/api/v1/regulations/{regulation_id}")
def api_update_regulation(regulation_id: int, body: RegulationUpdate):
    """更新法规"""
    db = SessionLocal()
    r = db.get(Regulation, regulation_id)
    if not r:
        db.close()
        raise HTTPException(404, "法规不存在")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    db.close()
    return {"id": r.id, "title": r.title, "status": "updated"}


@app.delete("/api/v1/regulations/{regulation_id}")
def api_delete_regulation(regulation_id: int):
    """删除法规及其条款、Markdown chunks，并保持事务原子性。"""
    db = SessionLocal()
    try:
        r = db.get(Regulation, regulation_id)
        if not r:
            raise HTTPException(404, "法规不存在")
        db.execute(sa_delete(RegulationChunk).where(RegulationChunk.regulation_id == regulation_id))
        db.execute(sa_delete(RegulationArticle).where(RegulationArticle.regulation_id == regulation_id))
        db.delete(r)
        db.commit()
        return {"id": regulation_id, "status": "deleted"}
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to delete regulation %s", regulation_id)
        raise HTTPException(500, "法规删除失败，事务已回滚") from exc
    finally:
        db.close()


@app.get("/api/v1/regulations/{regulation_id}/articles")
def api_list_articles(regulation_id: int):
    """列出法规的所有条款"""
    db = SessionLocal()
    r = db.get(Regulation, regulation_id)
    if not r:
        db.close()
        raise HTTPException(404, "法规不存在")
    articles = db.execute(select(RegulationArticle).where(RegulationArticle.regulation_id == regulation_id).order_by(RegulationArticle.sort_order, RegulationArticle.article_no)).scalars().all()
    db.close()
    return {"regulation_id": regulation_id, "title": r.title, "articles": [{"id": a.id, "chapter": a.chapter, "article_no": a.article_no, "paragraph_no": a.paragraph_no, "heading": a.heading, "text": a.text, "sort_order": a.sort_order} for a in articles]}


@app.post("/api/v1/regulations/{regulation_id}/articles")
def api_create_article(regulation_id: int, body: RegulationArticleCreate):
    """创建法规条款"""
    db = SessionLocal()
    r = db.get(Regulation, regulation_id)
    if not r:
        db.close()
        raise HTTPException(404, "法规不存在")
    try:
        article_data = body.model_dump()
        # The path is authoritative; do not allow a body/path mismatch to
        # attach an article to another regulation.
        article_data["regulation_id"] = regulation_id
        article = RegulationArticle(
            **article_data,
            search_text=f"{body.article_no} {body.heading} {body.text}",
        )
        db.add(article)
        db.commit()
        db.refresh(article)
        return {"id": article.id, "article_no": article.article_no, "status": "created"}
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to create regulation article for %s", regulation_id)
        raise HTTPException(500, "法规条款创建失败，事务已回滚") from exc
    finally:
        db.close()


@app.post("/api/v1/regulations/retrieve")
def api_retrieve_regulations(body: RegulationRetrieveRequest):
    """法规混合检索"""
    db = SessionLocal()
    results = retrieve_regulations(db, query=body.query, jurisdiction=body.jurisdiction, tax_type=body.tax_type, industry=body.industry, status=body.status, legal_level=body.legal_level, effective_date=body.effective_date, top_k=body.top_k, include_expired=body.include_expired, use_rerank=body.rerank)
    db.close()
    return {"query": body.query, "results": results, "count": len(results)}


@app.post("/api/v1/regulations/query")
def api_query_regulations(body: RegulationQueryRequest):
    """法规问答"""
    db = SessionLocal()
    regulations = retrieve_regulations(db, query=body.query, jurisdiction=body.jurisdiction, tax_type=body.tax_type, industry=body.industry, status=body.status, legal_level=body.legal_level, effective_date=body.effective_date, top_k=body.top_k, include_expired=body.include_expired, use_rerank=body.rerank)
    articles = []
    if regulations and len(regulations) <= 3:
        for reg in regulations:
            arts = retrieve_regulation_articles(db, reg["regulation_id"], body.query, top_k=5)
            articles.extend(arts)
    db.close()
    answer_result = answer_regulation_query(body.query, regulations, articles) if body.answer else {"query": body.query, "answer": "", "regulation_count": len(regulations)}
    return {"query": body.query, "answer": answer_result.get("answer", ""), "regulation_count": len(regulations), "results": regulations, "citations": answer_result.get("citations", [])}


# ==================== V1.2: 手工输入与联网核验 API =========

class RegulationVerifyRequest(BaseModel):
    title: str
    document_no: str = ""
    full_text: str = ""

class RegulationCustomSaveRequest(BaseModel):
    title: str
    document_no: str
    issuer: str = ""
    legal_level: str = "规范性文件"
    jurisdiction: str = "全国"
    tax_type: str = "全部税种"
    industry: str = "建筑业"
    publish_date: str = ""
    effective_date: str = ""
    status: str = "现行有效"
    full_text: str
    business_role: str = "construction"


@app.post("/api/v1/regulations/verify")
def api_verify_regulation(
    body: RegulationVerifyRequest,
    principal=Depends(require_web_or_service_role("admin", "operator")),
):
    """联网核验用户输入的法律法规真实性与条款完整性"""
    from app.services.regulation_verifier import verify_regulation_online
    if not body.title and not body.document_no and not body.full_text:
        raise HTTPException(400, "请输入法规标题、发文字号或正文内容")
    result = verify_regulation_online(
        title=body.title.strip(),
        document_no=body.document_no.strip(),
        full_text=body.full_text.strip()
    )
    return {
        "is_authentic": result.is_authentic,
        "confidence_score": result.confidence_score,
        "detected_articles_count": result.detected_articles_count,
        "missing_articles": result.missing_articles,
        "is_continuous": result.is_continuous,
        "status_assessment": result.status_assessment,
        "official_sources": result.official_sources,
        "structure_summary": result.structure_summary,
        "verification_notes": result.verification_notes,
    }


@app.post("/api/v1/regulations/save-custom")
def api_save_custom_regulation(
    body: RegulationCustomSaveRequest,
    principal=Depends(require_web_or_service_role("admin", "operator")),
):
    """保存用户手工录入的法规并自动生成向量切块"""
    from app.services.regulations_md_ingest import chunk_markdown, upsert_chunks
    db = SessionLocal()
    try:
        doc_no = body.document_no.strip() or body.title.strip()
        existing = db.scalar(select(Regulation).where(Regulation.document_no == doc_no))

        meta_dict = {
            "title": body.title,
            "document_no": doc_no,
            "issuer": body.issuer,
            "legal_level": body.legal_level,
            "jurisdiction": body.jurisdiction,
            "tax_types": [body.tax_type],
            "industries": [body.industry],
            "publish_date": body.publish_date,
            "effective_date": body.effective_date,
            "status": body.status,
            "business_role": body.business_role,
            "category": f"business_roles/{body.business_role}",
            "source": "用户手工录入/联网核验入库",
        }

        reg_dict = {
            "document_no": doc_no,
            "title": body.title,
            "issuer": body.issuer,
            "legal_level": body.legal_level,
            "jurisdiction": body.jurisdiction,
            "tax_type": body.tax_type,
            "industry": body.industry,
            "publish_date": body.publish_date,
            "effective_date": body.effective_date,
            "status": body.status,
            "full_text": body.full_text,
            "source": "用户录入",
            "version_label": "V1",
            "embedding_json": "[]",
            "search_text": f"{body.title} {doc_no} {body.full_text[:500]}",
            "metadata_json": json.dumps(meta_dict, ensure_ascii=False),
        }

        if existing:
            for k, v in reg_dict.items():
                setattr(existing, k, v)
            db.flush()
            reg_id = existing.id
        else:
            new_reg = Regulation(**reg_dict)
            db.add(new_reg)
            db.flush()
            reg_id = new_reg.id

        chunks = chunk_markdown(body.full_text, doc_no)
        upsert_chunks(db, RegulationChunk, reg_id, chunks)
        db.commit()
        return {"id": reg_id, "document_no": doc_no, "title": body.title, "chunk_count": len(chunks), "status": "saved"}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"入库失败: {e}")
    finally:
        db.close()


# ==================== 多模态 AI 智能提取与自动入库 API ====================

class URLIngestRequest(BaseModel):
    url: str

@app.post("/api/v1/regulations/ai-parse-url")
def api_ai_parse_url(
    body: URLIngestRequest,
    principal=Depends(require_web_or_service_role("admin", "operator")),
):
    """抓取网页 URL 内容，AI 提取法规元数据并执行联网真实性核验"""
    from app.services.ai_regulation_extractor import ai_extract_regulation_metadata, fetch_url_content
    from app.services.regulation_verifier import verify_regulation_online
    try:
        raw_text = fetch_url_content(body.url)
        if not raw_text or len(raw_text) < 30:
            raise HTTPException(400, "无法从指定网页提取有效法规文本，请检查网址。")

        meta = ai_extract_regulation_metadata(raw_text)
        verify_res = verify_regulation_online(
            title=meta.get("title", ""),
            document_no=meta.get("document_no", ""),
            full_text=raw_text
        )
        return {
            "meta": meta,
            "full_text": raw_text,
            "verification": {
                "is_authentic": verify_res.is_authentic,
                "confidence_score": verify_res.confidence_score,
                "detected_articles_count": verify_res.detected_articles_count,
                "missing_articles": verify_res.missing_articles,
                "is_continuous": verify_res.is_continuous,
                "status_assessment": verify_res.status_assessment,
                "official_sources": verify_res.official_sources,
                "structure_summary": verify_res.structure_summary,
                "verification_notes": verify_res.verification_notes,
            }
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"网页解析失败: {e}")


@app.post("/api/v1/regulations/ai-parse-file")
async def api_ai_parse_file(
    file: UploadFile = File(...),
    principal=Depends(require_web_or_service_role("admin", "operator")),
):
    """解析上传的 PDF / 图片 / Word / TXT 文件，AI 自动提取法规元数据并联网核验"""
    from app.services.ai_regulation_extractor import ai_extract_regulation_metadata, extract_file_content
    from app.services.regulation_verifier import verify_regulation_online
    try:
        content_bytes = await read_upload_limited(file)
        raw_text = extract_file_content(content_bytes, file.filename or "upload.pdf")
        if not raw_text or len(raw_text) < 20:
            raise HTTPException(400, "文件未能成功解析出文本内容。")

        meta = ai_extract_regulation_metadata(raw_text)
        verify_res = verify_regulation_online(
            title=meta.get("title", ""),
            document_no=meta.get("document_no", ""),
            full_text=raw_text
        )
        return {
            "filename": file.filename,
            "meta": meta,
            "full_text": raw_text,
            "verification": {
                "is_authentic": verify_res.is_authentic,
                "confidence_score": verify_res.confidence_score,
                "detected_articles_count": verify_res.detected_articles_count,
                "missing_articles": verify_res.missing_articles,
                "is_continuous": verify_res.is_continuous,
                "status_assessment": verify_res.status_assessment,
                "official_sources": verify_res.official_sources,
                "structure_summary": verify_res.structure_summary,
                "verification_notes": verify_res.verification_notes,
            }
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"文件解析提取失败: {e}")


# ==================== 目录一键批量导入与北大法宝 API 同步 ====================

class BatchDirImportRequest(BaseModel):
    dir_path: str
    recursive: bool = True
    default_role: str = "construction"


@app.post("/api/v1/regulations/batch-import-dir")
def api_batch_import_regulations_dir(
    body: BatchDirImportRequest,
    principal=Depends(require_web_or_service_role("admin", "operator")),
):
    """从指定本地/服务器目录一键批量扫描并结构化入库所有法规文件 (.md, .pdf, .docx, .txt)"""
    from app.services.regulations_batch_importer import batch_import_regulations_from_dir
    db = SessionLocal()
    try:
        res = batch_import_regulations_from_dir(
            db=db,
            dir_path=body.dir_path.strip(),
            recursive=body.recursive,
            default_role=body.default_role.strip(),
        )
        return res
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"批量目录导入失败: {e}") from e
    finally:
        db.close()


class PkulawSyncRequest(BaseModel):
    api_key: str = ""
    jurisdiction: str = "全国"
    categories: list[str] = ["建筑", "税务"]
    keywords: str = ""
    timeliness: str = "现行有效"
    limit: int = 20
    local_dir: str = ""


@app.post("/api/v1/regulations/pkulaw-sync")
def api_pkulaw_sync_regulations(
    body: PkulawSyncRequest,
    principal=Depends(require_web_or_service_role("admin", "operator")),
):
    """从北大法宝 (PKULaw) API 智能同步法规至本地目录，并自动切块向量化入库"""
    from app.services.pkulaw_sync_service import sync_pkulaw_regulations
    db = SessionLocal()
    try:
        res = sync_pkulaw_regulations(
            db=db,
            api_key=body.api_key.strip(),
            jurisdiction=body.jurisdiction.strip(),
            categories=body.categories,
            keywords=body.keywords.strip(),
            timeliness=body.timeliness.strip(),
            limit=body.limit,
            local_dir=body.local_dir.strip(),
        )
        return res
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"北大法宝同步失败: {e}") from e
    finally:
        db.close()

