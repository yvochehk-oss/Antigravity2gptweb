"""FastAPI main application - ProjectRAG V1.1.

Imports app construction from wiring.py and health components from health.py.
Routes are registered via the lifespan context in wiring.py.
"""
from pathlib import Path
import json
from typing import Optional
from pydantic import BaseModel

from fastapi import FastAPI, Request, Depends, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from .auth import require_web_auth


from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, delete as sa_delete, and_, or_

from .db import init_db, db_health, close_connections, SessionLocal
from .session import get_db
from .models import (
    Project, Document, Chunk, IngestJob, Entity, ExternalParty, Regulation, RegulationArticle,
    is_canonical_entity_code,
)
from .schemas import (
    ProjectCreate, ProjectSync, DocumentMetadataPatch,
    RetrieveRequest, QueryRequest, FolderImportRequest,
    EntityCreate, EntityPatch, EntityResponse, ExternalPartyCreate, ExternalPartyPatch,
    RegulationCreate, RegulationUpdate, RegulationArticleCreate,
    RegulationRetrieveRequest, RegulationQueryRequest,
)
from .services.documents import register_bytes, scan_folder
from .services.jobs import enqueue_parse, start_worker, stop_worker, process_next, get_worker_status
from .services.retrieval import retrieve, get_query_stats
from .services.llm import answer_with_llm
from .services.mineru_adapter import mineru_available
from .services.embeddings import embedding_runtime
from .services.reranker import reranker_runtime
from .services.extractor import extract_from_chunk, llm_extraction_available, ExtractionError
from .services.regulation_retrieval import (
    retrieve_regulations, retrieve_regulation_articles, answer_regulation_query,
)
from .services.tax_extraction import (
    ExtractTaxRequest, ExtractTaxResponse, ExtractedItem,
    EXTRACT_QUERY_TEMPLATES, EXTRACT_DOC_TYPE_FILTERS, EXTRACT_CATEGORY_FILTERS,
)
from .config import (
    AUTO_START_WORKER, IS_POSTGRES, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
)
from .logging_config import setup_logging, get_logger
from .config_validator import validate_config
from .middleware import RateLimitMiddleware, RequestIdMiddleware
from .security import RAGSecurityMiddleware, read_upload_limited, security_audit
from .services.storage import StorageError, PathTraversalError, validate_stored_file
from .observability import (
    REQUEST_ID_HEADER, get_request_id, new_request_id, set_request_id, reset_request_id,
)
from .wiring import (
    create_app,
    lifespan,
    BUSINESS_ROLE_META,
    BUSINESS_ROLE_ALIASES,
    _business_role_key,
    now,
)
from .health import get_health_components, worst_status

# Import v1.0 legacy modules
try:
    from facts_provider import setup_facts_provider
    from ai_review.routes import router as ai_review_router
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


from fastapi.middleware.cors import CORSMiddleware
from .routers.executive_mobile import router as executive_mobile_router

app = FastAPI(
    title="ProjectRAG",
    version="1.1.0",
    description="Project Knowledge RAG Service (merged v0.2-optimized + v1.0 facts/ai_review + v0.2 regulation retrieval)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(RAGSecurityMiddleware)

from app.routers.auth import router as auth_router
from app.routers.mounts import router as mounts_router

app.include_router(auth_router)
app.include_router(mounts_router)
app.include_router(executive_mobile_router)

if _HAS_V1_LEGACY:
    try:
        setup_facts_provider(app)
        app.include_router(ai_review_router)
        logger.info("v1.0 legacy routes registered")
    except Exception as _e:
        logger.warning(f"v1.0 legacy routes 注册失败: {_e}")


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ============================================
# Health & Status Endpoints
# ============================================

import time as _time


def _check_db_component() -> dict[str, object]:
    """Return the database component health."""
    started = _time.monotonic()
    try:
        from .db import SessionLocal
        from sqlalchemy import text
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
        from facts_provider import facts_provider as _fp
        from sqlalchemy import text
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
def health_ui(request: Request):
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
        for key, msg in [("waiting_mineru", "存在等待MinerU解析的资料"), ("parse_failed", "存在解析失败资料"), ("unclassified", "存在未分类资料，需要确认元数据"), ("duplicates", "存在重复文件，系统已阻止重复索引")]:
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
        coverage["tax_detail"] = dict(tax_cats)
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
    """Delete a document and its chunks."""
    from .services.ingest import cleanup_paths
    with get_db() as db:
        d = db.get(Document, document_id)
        if not d:
            raise HTTPException(404, "document not found")
        original_path = d.original_path
        parsed_dir = d.parsed_dir
        document_code = d.document_code
        db.execute(sa_delete(Chunk).where(Chunk.document_id == document_id))
        db.delete(d)
        db.commit()
    cleanup_paths(original_path, parsed_dir)
    security_audit(request, "document_delete", "success", document_id=document_id, subject=document_code)
    logger.info(f"Deleted document {document_id}: {document_code}")
    return {"deleted": True, "document_id": document_id}


@app.get("/api/v1/documents/{document_id}/original")
def api_original(document_id: int):
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
        db.add(party); db.commit(); db.refresh(party)
        return {"id": party.id, "code": party.code, "name": party.name}


@app.patch("/api/v1/external-parties/{party_id}")
def api_patch_external_party(party_id: int, body: ExternalPartyPatch):
    with get_db() as db:
        party = db.get(ExternalParty, party_id)
        if not party: raise HTTPException(404, "external party not found")
        for k, v in body.model_dump(exclude_none=True).items(): setattr(party, k, v)
        db.commit(); db.refresh(party)
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
        chunks = retrieve(db, pid, query, filters, body.top_k, use_rerank=False)
        errors: list[str] = []
        extracted_items: list[ExtractedItem] = []
        for chunk in chunks:
            chunk_text = chunk.get("text", "")
            if not chunk_text or len(chunk_text.strip()) < 20:
                errors.append(f"chunk {chunk['chunk_id']}: text too short, skipped")
                continue
            try:
                fields, confidence = extract_from_chunk(chunk_text, body.extract_type)
                extracted_items.append(ExtractedItem(source_chunk_id=chunk["chunk_id"], source_document_id=chunk["document_id"], filename=chunk["filename"], page_start=chunk.get("page_start"), page_end=chunk.get("page_end"), confidence=confidence, extract_type=body.extract_type, fields=fields))
            except ExtractionError as e:
                errors.append(f"chunk {chunk['chunk_id']}: {e}")
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
        docs = db.scalar(select(func.count(Document.id))) or 0
        indexed = db.scalar(select(func.count(Document.id)).where(Document.parse_status == "INDEXED")) or 0
        waiting = db.scalar(select(func.count(Document.id)).where(Document.parse_status.in_(["QUEUED", "WAITING_MINERU", "PARSE_FAILED", "UPLOADED"]))) or 0
        # 查询每个项目的凭证数与涉及的往来主体
        project_stats = {}
        for p in projects:
            p_docs = db.scalar(select(func.count(Document.id)).where(Document.project_id == p.id)) or 0
            p_indexed = db.scalar(select(func.count(Document.id)).where(Document.project_id == p.id, Document.parse_status == "INDEXED")) or 0
            p_ents = [
                r[0] for r in db.execute(
                    select(Document.entity_code).where(Document.project_id == p.id, Document.entity_code.is_not(None), Document.entity_code != "").distinct()
                ).all() if r[0]
            ]
            project_stats[p.id] = {
                "doc_count": p_docs,
                "indexed_count": p_indexed,
                "entity_count": len(p_ents),
                "entities": sorted(p_ents),
            }

        return templates.TemplateResponse(request, "dashboard.html", {
            "projects": projects,
            "project_stats": project_stats,
            "canonical_entities": canonical_entities,
            "entity_by_code": {x["entity_code"]: x for x in canonical_entities},
            "entity_summary": _entity_summary(canonical_entities),
            "docs": docs,
            "indexed": indexed,
            "waiting": waiting,
            "mineru": mineru_available(),
            "postgres": IS_POSTGRES,
            "worker": get_worker_status(),
        })


@app.post("/projects")
def web_create_project(project_code: str = Form(...), name: str = Form(...), external_system: str = Form(""), external_project_id: str = Form("")):
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
async def web_upload(project_id: int, file: UploadFile = File(...), document_type: str = Form(""), entity_code: str = Form(""), counterparty_code: str = Form(""), business_category: str = Form(""), period: str = Form("")):
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
def web_import_folder(project_id: int, path: str = Form(...), recursive: bool = Form(False)):
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
        return templates.TemplateResponse(request, "document.html", {"doc": d, "project": p, "chunks": chunks, "jobs": jobs, "active_page": "projects", "mineru": mineru_available(), "postgres": IS_POSTGRES})


@app.post("/documents/{document_id}/parse")
def web_parse(document_id: int):
    """Web: Re-parse document."""
    api_parse(document_id)
    return RedirectResponse(f"/documents/{document_id}", 303)


@app.get("/search", response_class=HTMLResponse)
def web_search(request: Request, project_id: Optional[int] = None, q: str = "", business_category: str = "", entity_code: str = "", business_role: str = ""):
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
def web_regulations(request: Request, q: str = "", entity: str = "", business_role: str = "", jurisdiction: str = "", level: str = "", status: str = ""):
    """Web: Regulations browser page."""
    category_aliases = {"entity_a": "construction", "entity_b": "trade", "entity_c": "labor", "entity_d": "equipment", "a": "construction", "b": "trade", "c": "labor", "d": "equipment", "construction": "construction", "trade": "trade", "labor": "labor", "equipment": "equipment"}
    requested_role = (business_role or entity or "").strip().lower()
    role_filter = category_aliases.get(requested_role, "")
    CATEGORY_META = {"construction": {"order": 1, "label": "建筑施工业务角色", "badge": "badge-cyan"}, "trade": {"order": 2, "label": "商贸物资业务角色", "badge": "badge-emerald"}, "labor": {"order": 3, "label": "建筑劳务业务角色", "badge": "badge-purple"}, "equipment": {"order": 4, "label": "工程设备业务角色", "badge": "badge-amber"}, "national_vat": {"order": 5, "label": "国家增值税", "badge": "badge-cyan"}, "national_other": {"order": 6, "label": "国家其他税", "badge": "badge-cyan"}, "sichuan": {"order": 7, "label": "四川省规定", "badge": "badge-emerald"}, "chengdu": {"order": 8, "label": "成都市公告", "badge": "badge-amber"}}
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
            cinfo = CATEGORY_META.get(cat, {"order": 99, "label": "其他法规", "badge": "badge-muted"})
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
def api_get_regulation(regulation_id: int):
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
    """删除法规及其条款"""
    db = SessionLocal()
    r = db.get(Regulation, regulation_id)
    if not r:
        db.close()
        raise HTTPException(404, "法规不存在")
    db.execute(sa_delete(RegulationArticle).where(RegulationArticle.regulation_id == regulation_id))
    db.delete(r)
    db.commit()
    db.close()
    return {"id": regulation_id, "status": "deleted"}


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


@app.post("/api/v1/regulations/{registration_id}/articles")
def api_create_article(regulation_id: int, body: RegulationArticleCreate):
    """创建法规条款"""
    db = SessionLocal()
    r = db.get(Regulation, regulation_id)
    if not r:
        db.close()
        raise HTTPException(404, "法规不存在")
    article = RegulationArticle(**body.model_dump(), search_text=f"{body.article_no} {body.heading} {body.text}")
    db.add(article)
    db.commit()
    db.refresh(article)
    db.close()
    return {"id": article.id, "article_no": article.article_no, "status": "created"}


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
def api_verify_regulation(body: RegulationVerifyRequest):
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
def api_save_custom_regulation(body: RegulationCustomSaveRequest):
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
def api_ai_parse_url(body: URLIngestRequest):
    """抓取网页 URL 内容，AI 提取法规元数据并执行联网真实性核验"""
    from app.services.ai_regulation_extractor import fetch_url_content, ai_extract_regulation_metadata
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
    except Exception as e:
        raise HTTPException(500, f"网页解析失败: {e}")


@app.post("/api/v1/regulations/ai-parse-file")
async def api_ai_parse_file(file: UploadFile = File(...)):
    """解析上传的 PDF / 图片 / Word / TXT 文件，AI 自动提取法规元数据并联网核验"""
    from app.services.ai_regulation_extractor import extract_file_content, ai_extract_regulation_metadata
    from app.services.regulation_verifier import verify_regulation_online
    try:
        content_bytes = await file.read()
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
    except Exception as e:
        raise HTTPException(500, f"文件解析提取失败: {e}")


