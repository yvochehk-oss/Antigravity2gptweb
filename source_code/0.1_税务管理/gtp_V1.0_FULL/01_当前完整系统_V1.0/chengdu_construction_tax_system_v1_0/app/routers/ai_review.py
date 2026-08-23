"""V0.2: 单环节 AI 检查路由。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import case, select

from ..ai import SCOPES, run_review
from ..audit import audit_from_request, current_actor
from ..db import SessionLocal
from ..models import (
    AIModelEndpoint,
    AIPromptTemplate,
    AIReviewJob,
    AIReviewResult,
    Project,
)
from ..templates import templates

router = APIRouter(tags=["AI审查"])


@router.get("/ai-review", response_class=HTMLResponse)
def ai_review_home(
    request: Request,
    project_id: int | None = None,
    scope: str | None = None,
):
    db = SessionLocal()
    projects = db.execute(select(Project)).scalars().all()
    endpoints = db.execute(
        select(AIModelEndpoint)
        .where(AIModelEndpoint.enabled == True)  # noqa: E712
        .order_by(case((AIModelEndpoint.adapter == "mock", 1), else_=0), AIModelEndpoint.id)
    ).scalars().all()
    jobs = db.execute(
        select(AIReviewJob).order_by(AIReviewJob.id.desc()).limit(50)
    ).scalars().all()
    project_map = {p.id: p for p in projects}
    endpoint_map = {e.id: e for e in db.execute(select(AIModelEndpoint).order_by(case((AIModelEndpoint.adapter == "mock", 1), else_=0), AIModelEndpoint.id)).scalars().all()}
    db.close()
    return templates.TemplateResponse(
        request,
        "ai_review.html",
    {

        "request": request, "projects": projects, "endpoints": endpoints,
        "jobs": jobs, "project_map": project_map, "endpoint_map": endpoint_map,
        "scopes": SCOPES, "selected_project_id": project_id,
        "selected_scope": scope,
    },
    )


@router.post("/ai-review/run")
def ai_review_run(
    request: Request,
    project_id: int = Form(...),
    scope: str = Form(...),
    endpoint_id: int = Form(...),
    user_instruction: str = Form(""),
):
    db = SessionLocal()
    if scope not in SCOPES:
        db.close()
        return RedirectResponse("/ai-review", status_code=303)

    job = AIReviewJob(
        project_id=project_id, scope=scope, endpoint_id=endpoint_id,
        user_instruction=user_instruction, status="pending",
        created_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc,
        ).isoformat(timespec="seconds"),
        actor=current_actor(request),
    )
    db.add(job); db.flush()
    audit_from_request(
        db, request, "AI_REVIEW_START", "AIReviewJob", job.id,
        f"project={project_id}, scope={scope}, endpoint={endpoint_id}",
    )
    db.commit()
    try:
        run_review(db, job)
        audit_from_request(
            db, request, "AI_REVIEW_COMPLETE", "AIReviewJob", job.id,
            f"status={job.status}",
        )
        db.commit()
    except Exception as e:
        audit_from_request(
            db, request, "AI_REVIEW_FAILED", "AIReviewJob", job.id, str(e),
        )
        db.commit()
    jid = job.id; db.close()
    return RedirectResponse(f"/ai-review/{jid}", status_code=303)


@router.get("/ai-review/{job_id}", response_class=HTMLResponse)
def ai_review_detail(request: Request, job_id: int):
    db = SessionLocal()
    job = db.get(AIReviewJob, job_id)
    if not job:
        db.close()
        return HTMLResponse("AI review job not found", status_code=404)
    project = db.get(Project, job.project_id)
    endpoint = db.get(AIModelEndpoint, job.endpoint_id)
    prompt_template = (
        db.get(AIPromptTemplate, job.prompt_template_id)
        if job.prompt_template_id else None
    )
    result = db.scalar(
        select(AIReviewResult).where(AIReviewResult.job_id == job.id)
    )
    findings: list = []; recommendations: list = []; gaps: list = []
    if result:
        for key, target in (
            ("findings_json", findings),
            ("recommendations_json", recommendations),
            ("data_gaps_json", gaps),
        ):
            try:
                target.extend(json.loads(getattr(result, key) or "[]"))
            except Exception:
                pass
    db.close()
    return templates.TemplateResponse(
        request,
        "ai_review_detail.html",
    {

        "request": request, "job": job, "project": project,
        "endpoint": endpoint, "result": result,
        "findings": findings, "recommendations": recommendations,
        "gaps": gaps, "scope_name": SCOPES.get(job.scope, job.scope),
        "prompt_template": prompt_template,
    },
    )


@router.get("/api/ai-review/{job_id}")
def api_ai_review(job_id: int):
    db = SessionLocal()
    job = db.get(AIReviewJob, job_id)
    if not job:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    result = db.scalar(
        select(AIReviewResult).where(AIReviewResult.job_id == job.id)
    )
    out = {"job": {
        "id": job.id, "project_id": job.project_id, "scope": job.scope,
        "status": job.status, "created_at": job.created_at,
        "started_at": job.started_at, "finished_at": job.finished_at,
        "input_digest": job.input_digest,
        "error_message": job.error_message,
        "parse_failed": job.parse_failed,
    }}
    if result:
        out["result"] = {
            "risk_level": result.risk_level, "score": float(result.score),
            "summary": result.summary,
            "findings": json.loads(result.findings_json or "[]"),
            "recommendations": json.loads(result.recommendations_json or "[]"),
            "data_gaps": json.loads(result.data_gaps_json or "[]"),
            "provider_name": result.provider_name,
            "model_name": result.model_name,
        }
    db.close()
    return out