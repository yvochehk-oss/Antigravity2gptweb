"""V0.2: 四流匹配 + 风险中心路由。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from ..templates import templates

from ..calc import matching_rows, scan_risks
from ..constants import CATEGORY_LABELS, RISK_CODE_LABELS, SEVERITY_LABELS
from ..db import SessionLocal
from ..models import Project

router = APIRouter()


@router.get("/matching", response_class=HTMLResponse)
def matching(request: Request, project_id: int | None = None):
    db = SessionLocal()
    ps = db.execute(select(Project)).scalars().all()
    pid = project_id or (ps[0].id if ps else None)
    rows = matching_rows(db, pid) if pid else []
    db.close()
    return templates.TemplateResponse(
        request,
        "matching.html",
        {
            "request": request, "projects": ps, "pid": pid, "rows": rows,
            "category_labels": CATEGORY_LABELS,
        },
    )


@router.get("/risks", response_class=HTMLResponse)
def risks(request: Request, project_id: int | None = None):
    db = SessionLocal()
    ps = db.execute(select(Project)).scalars().all()
    pid = project_id or (ps[0].id if ps else None)
    rows = scan_risks(db, pid) if pid else []
    db.close()
    return templates.TemplateResponse(
        request,
        "risks.html",
    {
        "request": request, "projects": ps, "pid": pid, "rows": rows,
        "severity_labels": SEVERITY_LABELS,
        "risk_code_labels": RISK_CODE_LABELS,
    },
    )