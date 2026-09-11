"""Project deletion router — RAG-owned data + Tax cross-call.

This router is intentionally registered BEFORE enforce_project_master_read_only
in main.py so the SSOT boundary can validate that this is the ONLY route
that mutates project-level data within the RAG service.

Architecture (SSOT-consistent):
  - Project Master (projects table row) is owned by Tax; RAG never touches it.
  - RAG owns: documents, chunks, ingest_jobs, canonical_facts, etc.
  - "删除项目" in RAG UI = wipe all RAG-owned data for this project
    + forward to Tax so Tax can wipe its own project-scoped records.

Flow (user clicks "彻底删除"):
  1. RAG validates admin session + project exists.
  2. RAG deletes all documents/chunks/ingest_jobs for this project (RAG-owned).
  3. RAG forwards the password to Tax's delete-data endpoint so Tax
     deletes contracts / invoices / cashflows / … for this project.
  4. RAG returns the combined result to the browser.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..auth import TaxPrincipal, require_web_role
from ..db import SessionLocal
from ..models import Chunk, Document, IngestJob, Project

router = APIRouter(prefix="/api/v1/projects", tags=["project-delete"])

# ---------------------------------------------------------------------------
# Tax endpoint URL — same host, different port (default 8921)
# ---------------------------------------------------------------------------
_TAX_HOST = os.getenv("TAX_HOST", "127.0.0.1")
_TAX_PORT = os.getenv("TAX_PORT", "8921")
_TAX_BASE_URL = f"http://{_TAX_HOST}:{_TAX_PORT}"


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class DeleteProjectRequest(BaseModel):
    password: str


class DeleteProjectResponse(BaseModel):
    project_id: int
    success: bool
    rag_deleted: dict[str, int]
    tax_forward_status: int | None
    tax_forward_body: dict[str, Any] | str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_raw_jwt(request: Request) -> str:
    """Extract the raw JWT string from the browser's cdjg_rag_token cookie.

    This JWT was issued by Tax and is trusted by Tax when presented as a
    ``Bearer`` token in the Authorization header.  Forwarding it here avoids
    re-implementing a shared secret exchange.
    """
    token = request.cookies.get("cdjg_rag_token", "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少有效的 Web 会话",
        )
    return token


def _forward_tax_delete(project_id: int, password: str, bearer_token: str) -> tuple[int, dict[str, Any] | str]:
    """Call Tax's POST /api/projects/{pid}/delete-data with the user's JWT."""
    url = f"{_TAX_BASE_URL}/api/projects/{project_id}/delete-data"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "User-Agent": "ProjectRAG/1.1 (project-delete forwarder)",
    }
    body = {"password": password}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=body)
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, resp.text or f"HTTP {resp.status_code}"
    except httpx.TimeoutException:
        return 599, {"error": "Tax 系统请求超时，请稍后重试"}
    except httpx.RequestError as exc:
        return 599, {"error": f"无法连接税务系统: {exc}"}


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@router.post(
    "/{project_id}/delete",
    response_model=DeleteProjectResponse,
    summary="彻底删除工程项目（RAG 侧数据 + 转发税务系统）",
    description=(
        "仅限 admin。执行两步原子删除：\n"
        "1. 删除 RAG 侧的 documents / chunks / ingest_jobs；\n"
        "2. 携带密码转发请求至税务系统，由税务系统完成其侧的项目数据删除。\n"
        "Project Master 行（projects 表）由税务系统持有，RAG 不触碰。"
    ),
)
def api_delete_project(
    project_id: int,
    body: DeleteProjectRequest,
    principal: TaxPrincipal = Depends(require_web_role("admin")),
    request: Request = None,
) -> DeleteProjectResponse:
    # 1. Verify project exists in RAG
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"项目 {project_id} 在 RAG 系统中不存在",
            )

        # 2. Count before deletion for the response
        doc_count = db.query(Document).filter(Document.project_id == project_id).count()
        chunk_count = db.query(Chunk).filter(Chunk.document_id.in_(
            db.query(Document.id).filter(Document.project_id == project_id)
        )).count()
        job_count = db.query(IngestJob).filter(IngestJob.document_id.in_(
            db.query(Document.id).filter(Document.project_id == project_id)
        )).count()

        # 3. Delete RAG-owned data (documents → chunks cascade → ingest_jobs)
        #    Delete chunks first (foreign key from ingest_jobs references chunks
        #    via document_id; ingest_jobs also references document_id directly)
        db.query(IngestJob).filter(
            IngestJob.document_id.in_(
                db.query(Document.id).filter(Document.project_id == project_id)
            )
        ).delete(synchronize_session=False)

        db.query(Chunk).filter(
            Chunk.document_id.in_(
                db.query(Document.id).filter(Document.project_id == project_id)
            )
        ).delete(synchronize_session=False)

        db.query(Document).filter(Document.project_id == project_id).delete(
            synchronize_session=False
        )

        db.commit()
        rag_deleted = {
            "documents": doc_count,
            "chunks": chunk_count,
            "ingest_jobs": job_count,
        }

    # 4. Forward to Tax so it can delete its own project-scoped data
    raw_jwt = _get_raw_jwt(request)
    tax_status, tax_body = _forward_tax_delete(project_id, body.password, raw_jwt)

    return DeleteProjectResponse(
        project_id=project_id,
        success=True,
        rag_deleted=rag_deleted,
        tax_forward_status=tax_status,
        tax_forward_body=tax_body,
    )
