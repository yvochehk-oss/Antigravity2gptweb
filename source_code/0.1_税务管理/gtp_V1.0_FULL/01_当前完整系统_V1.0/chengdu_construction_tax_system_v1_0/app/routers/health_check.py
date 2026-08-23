"""V0.2: 一键项目体检路由。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import case, select

from ..ai import HEALTH_PROFILES, SCOPES, run_health_check
from ..ai.timeutil import now_iso
from ..audit import audit_from_request, current_actor
from ..db import SessionLocal
from ..models import (
    AIConsensusReport,
    AIModelEndpoint,
    AIReviewBatch,
    AIReviewJob,
    AIReviewResult,
    Project,
)
from ..templates import templates

router = APIRouter(tags=["AI体检"])


@router.get("/health-check", response_class=HTMLResponse)
def health_check_home(request: Request, project_id: int | None = None):
    db = SessionLocal()
    projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
    endpoints = db.execute(
        select(AIModelEndpoint)
        .where(AIModelEndpoint.enabled == True)  # noqa: E712
        .order_by(case((AIModelEndpoint.adapter == "mock", 1), else_=0), AIModelEndpoint.id)
    ).scalars().all()
    batches = db.execute(
        select(AIReviewBatch).order_by(AIReviewBatch.id.desc()).limit(30)
    ).scalars().all()
    project_map = {p.id: p for p in projects}
    db.close()
    return templates.TemplateResponse(
        request,
        "health_check.html",
    {

        "request": request, "projects": projects, "endpoints": endpoints,
        "batches": batches, "project_map": project_map,
        "profiles": HEALTH_PROFILES, "selected_project_id": project_id,
        "profile_labels": {"quick": "快速", "standard": "标准", "deep": "深度"},
    },
    )


@router.post("/health-check/run")
def health_check_run(
    request: Request,
    project_id: int = Form(...),
    profile: str = Form("standard"),
    endpoint_ids: list[int] = Form(...),
    user_instruction: str = Form(""),
):
    db = SessionLocal()
    scopes = HEALTH_PROFILES.get(profile, HEALTH_PROFILES["standard"])
    batch = AIReviewBatch(
        project_id=project_id, profile=profile,
        scopes_json=json.dumps(scopes, ensure_ascii=False),
        endpoint_ids_json=json.dumps(endpoint_ids),
        user_instruction=user_instruction, status="pending",
        created_at=now_iso(), actor=current_actor(request),
    )
    db.add(batch); db.flush()
    audit_from_request(
        db, request, "AI_HEALTH_START", "AIReviewBatch", batch.id,
        f"project={project_id}, profile={profile}, endpoints={endpoint_ids}",
    )
    db.commit()
    try:
        run_health_check(db, batch)
        audit_from_request(
            db, request, "AI_HEALTH_COMPLETE", "AIReviewBatch", batch.id,
            f"status={batch.status}",
        )
        db.commit()
    except Exception as e:
        batch.status = "failed"
        batch.error_message = str(e)
        batch.finished_at = now_iso()
        audit_from_request(
            db, request, "AI_HEALTH_FAILED", "AIReviewBatch", batch.id, str(e),
        )
        db.commit()
    bid = batch.id; db.close()
    return RedirectResponse(f"/health-check/{bid}", status_code=303)


@router.get("/health-check/{batch_id}", response_class=HTMLResponse)
def health_check_detail(request: Request, batch_id: int):
    db = SessionLocal()
    batch = db.get(AIReviewBatch, batch_id)
    if not batch:
        db.close()
        return HTMLResponse("health check batch not found", status_code=404)
    project = db.get(Project, batch.project_id)
    jobs = db.execute(
        select(AIReviewJob)
        .where(AIReviewJob.batch_id == batch.id)
        .order_by(AIReviewJob.scope, AIReviewJob.endpoint_id)
    ).scalars().all()
    result_map: dict = {}
    endpoint_map = {
        e.id: e for e in db.execute(select(AIModelEndpoint)).scalars().all()
    }
    for j in jobs:
        rr = db.scalar(
            select(AIReviewResult).where(AIReviewResult.job_id == j.id)
        )
        result_map[j.id] = rr
    report = db.scalar(
        select(AIConsensusReport).where(AIConsensusReport.batch_id == batch.id)
    )
    common: list = []; differences: list = []
    recommendations: list = []; gaps: list = []
    if report:
        for key, target in (
            ("common_findings_json", common),
            ("differences_json", differences),
            ("recommendations_json", recommendations),
            ("data_gaps_json", gaps),
        ):
            try:
                target.extend(json.loads(getattr(report, key) or "[]"))
            except Exception:
                pass
    db.close()
    return templates.TemplateResponse(
        request,
        "health_check_detail.html",
    {

        "request": request, "batch": batch, "project": project,
        "jobs": jobs, "result_map": result_map,
        "endpoint_map": endpoint_map, "report": report,
        "common": common, "differences": differences,
        "recommendations": recommendations, "gaps": gaps,
        "scopes": SCOPES,
        "profile_labels": {"quick": "快速", "standard": "标准", "deep": "深度"},
    },
    )


@router.get("/api/health-check/{batch_id}")
def api_health_check(batch_id: int):
    db = SessionLocal()
    batch = db.get(AIReviewBatch, batch_id)
    if not batch:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    report = db.scalar(
        select(AIConsensusReport).where(AIConsensusReport.batch_id == batch.id)
    )
    out = {"batch": {
        "id": batch.id, "project_id": batch.project_id,
        "profile": batch.profile, "status": batch.status,
        "scopes": json.loads(batch.scopes_json or "[]"),
        "endpoint_ids": json.loads(batch.endpoint_ids_json or "[]"),
        "created_at": batch.created_at, "finished_at": batch.finished_at,
        "error_message": batch.error_message,
    }}
    if report:
        out["consensus"] = {
            "overall_risk": report.overall_risk, "score": float(report.score),
            "summary": report.summary,
            "common_findings": json.loads(report.common_findings_json or "[]"),
            "differences": json.loads(report.differences_json or "[]"),
            "recommendations": json.loads(report.recommendations_json or "[]"),
            "data_gaps": json.loads(report.data_gaps_json or "[]"),
        }
    db.close()
    return out