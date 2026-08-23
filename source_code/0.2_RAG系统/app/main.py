"""FastAPI main application with optimized session management and pagination."""
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse

from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func

from .db import init_db, db_health, close_connections
from .session import get_db
from .models import Project, Document, Chunk, IngestJob
from .schemas import (
    ProjectCreate, ProjectSync, DocumentMetadataPatch,
    RetrieveRequest, QueryRequest, FolderImportRequest,
    EntityCreate, EntityPatch, EntityResponse,
)
from .models import Project, Document, Chunk, IngestJob, Entity
from .services.documents import register_bytes, scan_folder
from .services.jobs import enqueue_parse, start_worker, stop_worker, process_next, get_worker_status
from .services.retrieval import retrieve, get_query_stats
from .services.llm import answer_with_llm
from .services.mineru_adapter import mineru_available
from .services.embeddings import embedding_runtime
from .services.reranker import reranker_runtime
from .services.extractor import extract_from_chunk, llm_extraction_available, ExtractionError
from .services.tax_extraction import (
    ExtractTaxRequest, ExtractTaxResponse, ExtractedItem,
    EXTRACT_QUERY_TEMPLATES, EXTRACT_DOC_TYPE_FILTERS, EXTRACT_CATEGORY_FILTERS,
)
from .config import (
    AUTO_START_WORKER, IS_POSTGRES, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
)
from .logging_config import setup_logging, get_logger
from .config_validator import validate_config
from .middleware import RateLimitMiddleware

# Setup logging
setup_logging()
logger = get_logger(__name__)


def now() -> str:
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting ProjectRAG V0.2 Optimized...")

    # Ensure required directories exist
    from .config_validator import ensure_directories
    ensure_directories()

    init_db()

    # Validate configuration
    errors = validate_config()
    if errors:
        logger.warning(f"Configuration warnings: {errors}")

    # Seed demo project if needed
    with get_db() as db:
        if not db.query(Project).count():
            db.add(Project(
                project_code="YB-DEMO-001",
                name="宜宾示范项目",
                external_system="construction-tax",
                external_project_id="1",
                note="ProjectRAG V0.2 Optimized 示范知识空间",
                created_at=now(),
                updated_at=now()
            ))
            db.commit()
            logger.info("Created demo project YB-DEMO-001")

    if AUTO_START_WORKER:
        start_worker()
        logger.info("Ingest worker auto-started")

    yield

    # Shutdown
    logger.info("Shutting down ProjectRAG...")
    stop_worker()
    close_connections()
    logger.info("Shutdown complete")


app = FastAPI(
    title="ProjectRAG",
    version="0.2.1-optimized",
    description="Optimized Project Knowledge RAG Service with MinerU + PostgreSQL/pgvector",
    lifespan=lifespan
)

# Add rate limit middleware
app.add_middleware(RateLimitMiddleware)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ============================================
# Health & Status Endpoints
# ============================================

@app.get("/api/v1/health")
def health():
    """Extended health check with validation status."""
    validation_errors = validate_config()

    return {
        "status": "ok" if not validation_errors else "degraded",
        "service": "project-rag",
        "version": "0.2.1-optimized",
        "mineru_available": mineru_available(),
        "database": db_health(),
        "embedding": embedding_runtime(),
        "reranker": reranker_runtime(),
        "llm_extraction": llm_extraction_available(),
        "worker": get_worker_status(),
        "worker_auto_start": AUTO_START_WORKER,
        "validation_errors": validation_errors
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

        return {
            "id": p.id,
            "project_code": p.project_code,
            "name": p.name,
            "status": p.status
        }


@app.get("/api/v1/projects/{project_id}")
def api_project(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
):
    """Get project details with paginated documents."""
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")

        # Count total documents
        total = db.scalar(
            select(func.count(Document.id)).where(Document.project_id == project_id)
        ) or 0

        # Paginated documents
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
            "id": p.id,
            "project_code": p.project_code,
            "name": p.name,
            "status": p.status,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            },
            "documents": [{
                "id": d.id,
                "document_code": d.document_code,
                "filename": d.filename,
                "parse_status": d.parse_status,
                "document_type": d.document_type,
                "business_category": d.business_category
            } for d in docs]
        }


@app.get("/api/v1/projects/{project_id}/audit")
def api_project_audit(project_id: int):
    """Project audit with coverage analysis and recommendations."""
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")

        docs = db.execute(
            select(Document).where(Document.project_id == project_id)
        ).scalars().all()

        counts = {
            "total": len(docs),
            "indexed": 0,
            "duplicates": 0,
            "queued": 0,
            "waiting_mineru": 0,
            "parse_failed": 0,
            "unclassified": 0,
            "missing_business_category": 0
        }

        types = set()
        cats = set()
        tax_cats = set()

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

        issues = []
        recommendations = []

        # Add issues
        for key, msg in [
            ("waiting_mineru", "存在等待MinerU解析的资料"),
            ("parse_failed", "存在解析失败资料"),
            ("unclassified", "存在未分类资料，需要确认元数据"),
            ("duplicates", "存在重复文件，系统已阻止重复索引")
        ]:
            if counts[key]:
                issues.append({"type": key.upper(), "count": counts[key], "message": msg})

        # Missing main contract
        if "main_contract" not in types:
            issues.append({"type": "MISSING_MAIN_CONTRACT", "count": 1, "message": "未识别到项目主合同"})

        # Coverage gaps
        coverage = {}
        for cat in ["material", "labor", "equipment", "subcontract", "tax"]:
            has_cat = cat in cats
            coverage[cat] = has_cat
            if not has_cat:
                issues.append({
                    "type": "CATEGORY_GAP",
                    "category": cat,
                    "message": f"尚未识别到 {cat} 类资料；如项目存在该业务，请补充"
                })
                recommendations.append(f"建议检查项目中是否存在 {cat} 类业务资料")

        # Tax coverage
        coverage["tax_detail"] = dict(tax_cats)
        coverage["missing_tax"] = len([t for t in ["vat", "enterprise_income", "individual_income"] if t not in tax_cats])

        # General recommendations
        if counts["unclassified"] > 0:
            recommendations.append("建议批量审核未分类文档，确保元数据准确")

        if counts["parse_failed"] > 0:
            recommendations.append("检查解析失败的文件，可能是格式问题或文件损坏")

        if counts["queued"] > counts["indexed"]:
            recommendations.append("仍有文档在队列中等待处理，请确认Worker正在运行")

        return {
            "project_id": project_id,
            "project_code": p.project_code,
            "counts": counts,
            "coverage": coverage,
            "issues": issues,
            "recommendations": recommendations
        }


# ============================================
# Document Endpoints
# ============================================

@app.post("/api/v1/documents/upload")
async def api_upload_document(
    project_id: int = Form(...),
    file: UploadFile = File(...),
    document_type: str = Form(""),
    entity_code: str = Form(""),
    counterparty_code: str = Form(""),
    business_category: str = Form(""),
    contract_no: str = Form(""),
    period: str = Form(""),
    auto_parse: bool = Form(True)
):
    """Upload a document with metadata."""
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")

        data = await file.read()
        d, jid = register_bytes(
            db, p, file.filename or "upload", data,
            {
                "document_type": document_type,
                "entity_code": entity_code,
                "counterparty_code": counterparty_code,
                "business_category": business_category,
                "contract_no": contract_no,
                "period": period
            },
            auto_parse
        )

        db.refresh(d)

        return {
            "id": d.id,
            "document_code": d.document_code,
            "filename": d.filename,
            "parse_status": d.parse_status,
            "parse_message": d.parse_message,
            "duplicate_of_id": d.duplicate_of_id,
            "job_id": jid
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

        return {
            "project_id": pid,
            "path": body.path,
            "files": rows,
            "count": len(rows),
            "imported": sum(1 for r in rows if "error" not in r),
            "failed": sum(1 for r in rows if "error" in r)
        }


@app.get("/api/v1/documents/{document_id}")
def api_document(document_id: int):
    """Get document details."""
    with get_db() as db:
        d = db.get(Document, document_id)
        if not d:
            raise HTTPException(404, "document not found")

        n = db.scalar(select(func.count(Chunk.id)).where(Chunk.document_id == document_id)) or 0

        out = {
            k: getattr(d, k) for k in [
                "id", "project_id", "document_code", "filename", "file_type",
                "file_hash", "size_bytes", "parse_status", "parse_message",
                "document_type", "entity_code", "counterparty_code",
                "business_category", "contract_no", "period", "document_date",
                "confidentiality", "version_label", "version_status",
                "duplicate_of_id", "metadata_confidence", "metadata_source",
                "parse_attempts"
            ]
        }
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

        return {
            "id": d.id,
            "document_code": d.document_code,
            "document_type": d.document_type,
            "entity_code": d.entity_code,
            "counterparty_code": d.counterparty_code,
            "business_category": d.business_category,
            "period": d.period
        }


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

        return {
            "id": d.id,
            "parse_status": d.parse_status,
            "job_id": job.id
        }


@app.delete("/api/v1/documents/{document_id}")
def api_delete_document(document_id: int):
    """Delete a document and its chunks."""
    from .services.ingest import cleanup_document_files

    with get_db() as db:
        d = db.get(Document, document_id)
        if not d:
            raise HTTPException(404, "document not found")

        # Extract path info before delete (needed for cleanup)
        original_path = d.original_path
        parsed_dir = d.parsed_dir
        document_code = d.document_code

        # Delete chunks
        db.execute(func.delete(Chunk).where(Chunk.document_id == document_id))

        # Delete document record
        db.delete(d)
        db.commit()

    # Cleanup files (outside transaction and db session)
    cleanup_document_files(original_path, parsed_dir)

    logger.info(f"Deleted document {document_id}: {document_code}")

    return {"deleted": True, "document_id": document_id}


@app.get("/api/v1/documents/{document_id}/original")
def api_original(document_id: int):
    """Download original document file."""
    with get_db() as db:
        d = db.get(Document, document_id)
        if not d or not Path(d.original_path).exists():
            raise HTTPException(404, "file not found")

        path = d.original_path
        filename = d.filename
        return FileResponse(path, filename=filename)


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

        return {
            "id": j.id,
            "document_id": j.document_id,
            "job_type": j.job_type,
            "status": j.status,
            "attempts": j.attempts,
            "max_attempts": j.max_attempts,
            "message": j.message,
            "last_error": j.last_error,
            "created_at": j.created_at,
            "started_at": j.started_at,
            "finished_at": j.finished_at,
            "next_retry_at": j.next_retry_at
        }


@app.post("/api/v1/jobs/process-next")
def api_process_next():
    """Process next job in queue."""
    return {"processed": process_next()}


@app.get("/api/v1/jobs")
def api_jobs(
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200)
):
    """List jobs with optional status filter and pagination."""
    with get_db() as db:
        stmt = select(IngestJob).order_by(IngestJob.id.desc())

        if status:
            stmt = stmt.where(IngestJob.status == status)

        total = db.scalar(select(func.count(IngestJob.id))) if not status else \
                db.scalar(select(func.count(IngestJob.id)).where(IngestJob.status == status))

        offset = (page - 1) * page_size
        jobs = db.execute(stmt.offset(offset).limit(page_size)).scalars().all()

        total_pages = (total + page_size - 1) // page_size if total else 0

        return {
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_items": total or 0,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            },
            "items": [{
                "id": j.id,
                "document_id": j.document_id,
                "job_type": j.job_type,
                "status": j.status,
                "attempts": j.attempts,
                "message": j.message,
                "created_at": j.created_at,
                "finished_at": j.finished_at
            } for j in jobs]
        }


# ============================================
# Entity (往来单位) Endpoints
# ============================================

@app.get("/api/v1/entities")
def api_entities(
    q: str = Query("", description="Search by name"),
    industry: str = Query("", description="Filter by industry"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200)
):
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

        return {
            "page": page,
            "page_size": page_size,
            "total_items": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "items": [{
                "id": e.id,
                "name": e.name,
                "short_name": e.short_name,
                "entity_type": e.entity_type,
                "industry": e.industry,
                "legal_representative": e.legal_representative,
                "registered_capital": e.registered_capital,
                "establishment_date": e.establishment_date,
                "registration_authority": e.registration_authority,
                "source": e.source,
                "created_at": e.created_at,
            } for e in rows]
        }


@app.get("/api/v1/entities/{entity_id}")
def api_entity(entity_id: int):
    """Get entity details."""
    with get_db() as db:
        e = db.get(Entity, entity_id)
        if not e:
            raise HTTPException(404, "entity not found")
        return {
            "id": e.id,
            "name": e.name,
            "short_name": e.short_name,
            "entity_type": e.entity_type,
            "industry": e.industry,
            "legal_representative": e.legal_representative,
            "legal_rep_id": e.legal_rep_id,
            "legal_rep_phone": e.legal_rep_phone,
            "shareholders": e.shareholders,
            "supervisor": e.supervisor,
            "finance_officer": e.finance_officer,
            "registered_capital": e.registered_capital,
            "establishment_date": e.establishment_date,
            "acquisition_date": e.acquisition_date,
            "registration_authority": e.registration_authority,
            "registration_number": e.registration_number,
            "unified_social_credit_code": e.unified_social_credit_code,
            "business_scope": e.business_scope,
            "contributed_legal": e.contributed_legal,
            "contributed_shareholder": e.contributed_shareholder,
            "note": e.note,
            "source": e.source,
            "data_as_of": e.data_as_of,
            "created_at": e.created_at,
            "updated_at": e.updated_at,
        }


@app.post("/api/v1/entities")
def api_create_entity(body: EntityCreate):
    """Create a new entity."""
    with get_db() as db:
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

        for k, v in body.model_dump(exclude_none=True).items():
            setattr(e, k, v)
        e.updated_at = now()
        db.commit()
        db.refresh(e)
        logger.info(f"Updated entity {entity_id}: {e.name}")
        return {"id": e.id, "name": e.name, "industry": e.industry}


@app.post("/api/v1/entities/batch")
def api_batch_create_entities(body: list[EntityCreate]):
    """Batch create or update entities (upsert by name)."""
    with get_db() as db:
        results = []
        for item in body:
            existing = db.scalar(select(Entity).where(Entity.name == item.name))
            if existing:
                # Update existing
                for k, v in item.model_dump().items():
                    if v and v != "" and v != 0:
                        setattr(existing, k, v)
                existing.updated_at = now()
                results.append({"id": existing.id, "name": existing.name, "action": "updated"})
            else:
                e = Entity(**item.model_dump(), created_at=now(), updated_at=now())
                db.add(e)
                results.append({"name": item.name, "action": "created"})

        db.commit()
        logger.info(f"Batch upserted {len(results)} entities")
        return {"total": len(results), "items": results}


@app.get("/api/v1/entities/summary")
def api_entities_summary():
    """Get summary of all entities by industry."""
    with get_db() as db:
        rows = db.execute(
            select(Entity.industry, func.count(Entity.id))
            .group_by(Entity.industry)
            .order_by(Entity.industry)
        ).all()
        total = db.scalar(select(func.count(Entity.id))) or 0
        return {
            "total": total,
            "by_industry": [{"industry": r[0] or "未分类", "count": r[1]} for r in rows]
        }


# ============================================
# Retrieval Endpoints
# ============================================

@app.post("/api/v1/retrieve")
def api_retrieve(body: RetrieveRequest):
    """Retrieve relevant document chunks."""
    with get_db() as db:
        pid = _resolve_project(db, body.project_id, body.project_code)
        out = retrieve(db, pid, body.query, body.filters, body.top_k, body.rerank)

        return {
            "project_id": pid,
            "query": body.query,
            "results": out
        }


@app.post("/api/v1/query")
def api_query(body: QueryRequest):
    """Retrieve and optionally generate answer."""
    with get_db() as db:
        pid = _resolve_project(db, body.project_id, body.project_code)
        evidence = retrieve(db, pid, body.query, body.filters, body.top_k, body.rerank)

    answer = ""
    if body.answer:
        answer = answer_with_llm(body.query, evidence)

    return {
        "project_id": pid,
        "query": body.query,
        "answer": answer,
        "citations": [{
            "index": i + 1,
            "document_id": x["document_id"],
            "filename": x["filename"],
            "page_start": x["page_start"],
            "page_end": x["page_end"],
            "heading_path": x["heading_path"],
            "chunk_id": x["chunk_id"]
        } for i, x in enumerate(evidence)],
        "results": evidence
    }


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
    """Extract structured tax data (invoices, contracts, payments) from project documents.

    This endpoint:
    1. Retrieves relevant document chunks from the RAG knowledge base
    2. Uses AI to extract structured fields from each chunk
    3. Returns structured data with confidence scores and source citations

    The extracted data can be used to populate the construction tax management system.
    """
    with get_db() as db:
        pid = _resolve_project(db, body.project_id, body.project_code)

        # Build retrieval filters from request
        filters = {}
        if body.period_start:
            filters["period"] = body.period_start
        if body.entity_code:
            filters["entity_code"] = body.entity_code
        if body.counterparty_code:
            filters["counterparty_code"] = body.counterparty_code

        # Apply document_type filter if configured
        doc_types = EXTRACT_DOC_TYPE_FILTERS.get(body.extract_type, [])
        if doc_types:
            filters["document_type"] = doc_types

        # Use domain-specific query for this extract_type
        query = EXTRACT_QUERY_TEMPLATES.get(body.extract_type, body.extract_type)

        # Retrieve document chunks
        chunks = retrieve(db, pid, query, filters, body.top_k, use_rerank=False)

        errors: list[str] = []
        extracted_items: list[ExtractedItem] = []

        # Extract from each chunk
        for chunk in chunks:
            chunk_text = chunk.get("text", "")
            if not chunk_text or len(chunk_text.strip()) < 20:
                errors.append(f"chunk {chunk['chunk_id']}: text too short, skipped")
                continue

            try:
                fields, confidence = extract_from_chunk(chunk_text, body.extract_type)
                extracted_items.append(ExtractedItem(
                    source_chunk_id=chunk["chunk_id"],
                    source_document_id=chunk["document_id"],
                    filename=chunk["filename"],
                    page_start=chunk.get("page_start"),
                    page_end=chunk.get("page_end"),
                    confidence=confidence,
                    extract_type=body.extract_type,
                    fields=fields,
                ))
            except ExtractionError as e:
                errors.append(f"chunk {chunk['chunk_id']}: {e}")

    return ExtractTaxResponse(
        project_id=pid,
        extract_type=body.extract_type,
        query_used=query,
        total_chunks=len(chunks),
        total_extracted=len(extracted_items),
        extracted_items=extracted_items,
        errors=errors,
        llm_available=llm_extraction_available(),
    )


# ============================================
# Helper Functions
# ============================================

def _resolve_project(db, project_id, project_code):
    """Resolve project ID from ID or code."""
    p = db.get(Project, project_id) if project_id else \
        db.scalar(select(Project).where(Project.project_code == project_code)) if project_code else None

    if not p:
        raise HTTPException(404, "project not found")

    return p.id


# ============================================
# Web UI Endpoints
# ============================================

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    """Dashboard page."""
    with get_db() as db:
        projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
        docs = db.scalar(select(func.count(Document.id))) or 0
        indexed = db.scalar(select(func.count(Document.id)).where(Document.parse_status == "INDEXED")) or 0
        waiting = db.scalar(
            select(func.count(Document.id)).where(
                Document.parse_status.in_(["QUEUED", "WAITING_MINERU", "PARSE_FAILED", "UPLOADED"])
            )
        ) or 0

        return templates.TemplateResponse(
            request, "dashboard.html",
            {
                "projects": projects,
                "docs": docs,
                "indexed": indexed,
                "waiting": waiting,
                "mineru": mineru_available(),
                "postgres": IS_POSTGRES,
                "worker": get_worker_status()
            }
        )


@app.post("/projects")
def web_create_project(
    project_code: str = Form(...),
    name: str = Form(...),
    external_system: str = Form(""),
    external_project_id: str = Form("")
):
    """Web: Create project."""
    with get_db() as db:
        existing = db.scalar(select(Project).where(Project.project_code == project_code))
        if not existing:
            db.add(Project(
                project_code=project_code,
                name=name,
                external_system=external_system,
                external_project_id=external_project_id,
                created_at=now(),
                updated_at=now()
            ))
            db.commit()
    return RedirectResponse("/", 303)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def web_project(request: Request, project_id: int):
    """Web: Project detail page."""
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            return HTMLResponse("project not found", 404)

        # Limit to recent 100 documents for display
        docs = db.execute(
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.id.desc())
            .limit(100)
        ).scalars().all()

        jobs = db.execute(
            select(IngestJob)
            .join(Document, IngestJob.document_id == Document.id)
            .where(Document.project_id == project_id)
            .order_by(IngestJob.id.desc())
            .limit(30)
        ).scalars().all()

        return templates.TemplateResponse(
            request, "project.html",
            {"project": p, "documents": docs, "jobs": jobs, "mineru": mineru_available()}
        )


@app.post("/projects/{project_id}/upload")
async def web_upload(
    project_id: int,
    file: UploadFile = File(...),
    document_type: str = Form(""),
    entity_code: str = Form(""),
    counterparty_code: str = Form(""),
    business_category: str = Form(""),
    period: str = Form("")
):
    """Web: Upload document."""
    # Read file data manually since non-async inner function
    data = await file.read()
    # Use the synchronous logic directly to avoid async/sync issues
    with get_db() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")
        try:
            register_bytes(
                db, p, file.filename or "upload", data,
                {
                    "document_type": document_type,
                    "entity_code": entity_code,
                    "counterparty_code": counterparty_code,
                    "business_category": business_category,
                    "contract_no": "",
                    "period": period
                },
                True
            )
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            raise HTTPException(500, f"Upload failed: {e}")
    return RedirectResponse(f"/projects/{project_id}", 303)


@app.post("/projects/{project_id}/import-folder")
def web_import_folder(project_id: int, path: str = Form(...), recursive: bool = Form(False)):
    """Web: Import folder."""
    api_import_folder(FolderImportRequest(
        project_id=project_id,
        path=path,
        recursive=recursive,
        auto_parse=True
    ))
    return RedirectResponse(f"/projects/{project_id}", 303)


@app.get("/documents/{document_id}", response_class=HTMLResponse)
def web_document(request: Request, document_id: int):
    """Web: Document detail page."""
    with get_db() as db:
        d = db.get(Document, document_id)
        if not d:
            return HTMLResponse("document not found", 404)

        chunks = db.execute(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
            .limit(50)
        ).scalars().all()

        p = db.get(Project, d.project_id)
        jobs = db.execute(
            select(IngestJob)
            .where(IngestJob.document_id == document_id)
            .order_by(IngestJob.id.desc())
            .limit(20)
        ).scalars().all()

        return templates.TemplateResponse(
            request, "document.html",
            {"doc": d, "project": p, "chunks": chunks, "jobs": jobs}
        )


@app.post("/documents/{document_id}/parse")
def web_parse(document_id: int):
    """Web: Re-parse document."""
    api_parse(document_id)
    return RedirectResponse(f"/documents/{document_id}", 303)


@app.get("/search", response_class=HTMLResponse)
def web_search(
    request: Request,
    project_id: Optional[int] = None,
    q: str = "",
    business_category: str = "",
    entity_code: str = ""
):
    """Web: Search page."""
    with get_db() as db:
        projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
        results = []

        if project_id and q:
            filters = {}
            if business_category:
                filters["business_category"] = business_category
            if entity_code:
                filters["entity_code"] = entity_code

            results = retrieve(db, project_id, q, filters, 12, True)

        return templates.TemplateResponse(
            request, "search.html",
            {
                "projects": projects,
                "project_id": project_id,
                "q": q,
                "business_category": business_category,
                "entity_code": entity_code,
                "results": results
            }
        )
