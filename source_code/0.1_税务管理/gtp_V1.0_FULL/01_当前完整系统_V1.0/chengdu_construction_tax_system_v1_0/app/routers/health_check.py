"""V0.2: 一键项目体检路由。"""
from __future__ import annotations

import json
from contextlib import suppress

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from ..ai import (
    HEALTH_PROFILES,
    SCOPES,
    claim_health_batch,
    enqueue_health_check,
    fail_health_batch,
    recover_stale_health_batches,
)
from ..ai.adapter import endpoint_is_allowed
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
    try:
        recover_stale_health_batches(db)
        projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
        endpoints = db.execute(
            select(AIModelEndpoint)
            .where(AIModelEndpoint.enabled == True)  # noqa: E712
            .order_by(AIModelEndpoint.id)
        ).scalars().all()
        endpoints = [e for e in endpoints if endpoint_is_allowed(e)]
        batches = db.execute(
            select(AIReviewBatch).order_by(AIReviewBatch.id.desc()).limit(30)
        ).scalars().all()
        project_map = {p.id: p for p in projects}
    finally:
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
    endpoint_ids: list[int] = Form(...),  # noqa: B008
    user_instruction: str = Form(""),
):
    db = SessionLocal()
    scopes = HEALTH_PROFILES.get(profile, HEALTH_PROFILES["standard"])
    try:
        recover_stale_health_batches(db)
        batch, created = claim_health_batch(
            db,
            project_id=project_id,
            profile=profile,
            scopes=scopes,
            endpoint_ids=endpoint_ids,
            user_instruction=user_instruction,
            actor=current_actor(request),
        )
        bid = batch.id
        if created:
            audit_from_request(
                db, request, "AI_HEALTH_START", "AIReviewBatch", bid,
                f"project={project_id}, profile={profile}, endpoints={endpoint_ids}",
            )
            db.commit()
        enqueue_health_check(bid)
    except BaseException as exc:
        error_text = str(exc).replace("\x00", " ").replace("\n", " ")[:10000]
        with suppress(Exception):
            db.rollback()
        if "bid" not in locals():
            raise
        fail_health_batch(bid, error_text or exc.__class__.__name__)
    finally:
        db.close()
    # Actual model work is outside this HTTP request.  A client disconnect
    # cannot strand the request thread in a long endpoint retry loop.
    return RedirectResponse(f"/health-check/{bid}", status_code=303)


@router.get("/health-check/{batch_id}", response_class=HTMLResponse)
def health_check_detail(request: Request, batch_id: int):
    db = SessionLocal()
    recover_stale_health_batches(db)
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
        if endpoint_is_allowed(e)
    }
    for j in jobs:
        rr = db.scalar(
            select(AIReviewResult).where(AIReviewResult.job_id == j.id)
        )
        result_map[j.id] = rr
    report = db.scalar(
        select(AIConsensusReport).where(AIConsensusReport.batch_id == batch.id)
    )
    common: list = []
    differences: list = []
    recommendations: list = []
    gaps: list = []
    if report:
        for key, target in (
            ("common_findings_json", common),
            ("differences_json", differences),
            ("recommendations_json", recommendations),
            ("data_gaps_json", gaps),
        ):
            with suppress(Exception):
                target.extend(json.loads(getattr(report, key) or "[]"))
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
    recover_stale_health_batches(db)
    batch = db.get(AIReviewBatch, batch_id)
    if not batch:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    report = db.scalar(
        select(AIConsensusReport).where(AIConsensusReport.batch_id == batch.id)
    )
    jobs = db.execute(
        select(AIReviewJob).where(AIReviewJob.batch_id == batch.id)
    ).scalars().all()
    failed_jobs = [job for job in jobs if job.status == "failed"]
    if batch.status == "completed" and not failed_jobs:
        status = "READY"
    elif batch.status == "failed" or (
        jobs and not any(job.status == "completed" for job in jobs)
    ):
        status = "UNAVAILABLE"
    else:
        status = "DEGRADED"
    data_gaps: list[str] = []
    if report:
        with suppress(Exception):
            data_gaps.extend(json.loads(report.data_gaps_json or "[]"))
    for job in failed_jobs:
        if job.error_message and job.error_message not in data_gaps:
            data_gaps.append(job.error_message)
    if batch.error_message and batch.error_message not in data_gaps:
        data_gaps.append(batch.error_message)
    out = {"batch": {
        "id": batch.id, "project_id": batch.project_id,
        "profile": batch.profile, "status": batch.status,
        "scopes": json.loads(batch.scopes_json or "[]"),
        "endpoint_ids": json.loads(batch.endpoint_ids_json or "[]"),
        "created_at": batch.created_at, "finished_at": batch.finished_at,
        "error_message": batch.error_message,
    }, "status": status,
        "requires_manual_review": status != "READY",
        "data_gaps": data_gaps,
    }
    # Expose the endpoint that actually produced each result.  A health batch
    # may have been requested with a primary endpoint while Review routed to a
    # same-group fallback, so ``AIReviewJob.endpoint_id`` alone is not enough.
    endpoint_by_name = {
        endpoint.name: endpoint.id
        for endpoint in db.execute(select(AIModelEndpoint)).scalars().all()
    }
    out["jobs"] = []
    for job in jobs:
        result = db.scalar(
            select(AIReviewResult).where(AIReviewResult.job_id == job.id),
        )
        if result is None:
            out["jobs"].append({
                "id": job.id,
                "scope": job.scope,
                "requested_endpoint_id": job.endpoint_id,
                "status": job.status,
            })
            continue
        result_gaps = json.loads(result.data_gaps_json or "[]")
        out["jobs"].append({
            "id": job.id,
            "scope": job.scope,
            "requested_endpoint_id": job.endpoint_id,
            "actual_endpoint_id": endpoint_by_name.get(result.provider_name),
            "endpoint_name": result.provider_name,
            "model": result.model_name,
            "status": job.status,
            "fallback_used": any("后备端点" in str(gap) for gap in result_gaps),
        })
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
