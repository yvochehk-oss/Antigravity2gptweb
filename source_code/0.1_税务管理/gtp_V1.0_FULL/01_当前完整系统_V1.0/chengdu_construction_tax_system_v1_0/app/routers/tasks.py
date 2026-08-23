"""V0.2: 整改任务中心路由。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, select

from ..ai import SCOPES, recheck_task
from ..ai.timeutil import now_iso
from ..audit import audit_from_request, current_actor
from ..constants import TASK_STATUS_LABELS, TASK_STATUSES
from ..db import SessionLocal
from ..models import AIModelEndpoint, Project, RemediationTask
from ..templates import templates

router = APIRouter()


@router.get("/tasks", response_class=HTMLResponse)
def task_center(request: Request):
    db = SessionLocal()
    rows = db.execute(
        select(RemediationTask).order_by(RemediationTask.id.desc())
    ).scalars().all()
    projects = {p.id: p for p in db.execute(select(Project)).scalars().all()}
    endpoints = db.execute(
        select(AIModelEndpoint)
        .where(AIModelEndpoint.enabled == True)  # noqa: E712
        .order_by(case((AIModelEndpoint.adapter == "mock", 1), else_=0), AIModelEndpoint.id)
    ).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "tasks.html",
    {

        "request": request, "rows": rows, "projects": projects,
        "endpoints": endpoints, "scopes": SCOPES,
        "status_labels": TASK_STATUS_LABELS,
    },
    )


@router.post("/tasks/create")
def task_create(
    request: Request,
    project_id: int = Form(...),
    scope: str = Form("whole_project"),
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("P2"),
    owner_role: str = Form("项目财务/商务"),
    source_job_id: int | None = Form(None),
    source_batch_id: int | None = Form(None),
):
    db = SessionLocal()
    actor = current_actor(request)
    x = RemediationTask(
        project_id=project_id, scope=scope,
        source_job_id=source_job_id, source_batch_id=source_batch_id,
        title=title[:200], description=description,
        priority=priority, owner_role=owner_role,
        status="open", created_at=now_iso(),
        updated_at=now_iso(), actor=actor,
    )
    db.add(x); db.flush()
    audit_from_request(
        db, request, "CREATE", "RemediationTask", x.id,
        f"{priority}/{title[:100]}",
    )
    db.commit(); db.close()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/status")
def task_status(request: Request, task_id: int, status: str = Form(...)):
    db = SessionLocal()
    x = db.get(RemediationTask, task_id)
    if x and status in TASK_STATUSES:
        x.status = status
        x.updated_at = now_iso()
        if status in {"verified", "closed"}:
            x.closed_at = now_iso()
        audit_from_request(
            db, request, "UPDATE", "RemediationTask", x.id, f"status={status}",
        )
        db.commit()
    db.close()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/recheck")
def task_recheck(
    request: Request, task_id: int, endpoint_id: int = Form(...),
):
    db = SessionLocal()
    x = db.get(RemediationTask, task_id)
    if not x:
        db.close()
        return RedirectResponse("/tasks", status_code=303)
    try:
        job = recheck_task(db, x, endpoint_id)
        audit_from_request(
            db, request, "AI_RECHECK", "RemediationTask", x.id,
            f"job={job.id}",
        )
        db.commit(); jid = job.id
    except Exception as e:
        audit_from_request(
            db, request, "AI_RECHECK_FAILED", "RemediationTask", x.id,
            str(e),
        )
        db.commit(); db.close()
        return RedirectResponse("/tasks", status_code=303)
    db.close()
    return RedirectResponse(f"/ai-review/{jid}", status_code=303)