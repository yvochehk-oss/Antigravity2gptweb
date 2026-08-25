"""V0.2: JSON API 路由。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..calc import four_flow_evidence_completeness, matching_rows, project_summary
from ..db import SessionLocal
from ..dependencies import require_role
from ..models import Project

_LOGGER = logging.getLogger(__name__)
_reader_dependency = Depends(require_role("admin", "operator"))

router = APIRouter()


@router.get("/api/projects/{pid}")
def api_project(pid: int, _user=_reader_dependency):
    db = SessionLocal()
    try:
        s = project_summary(db, pid)
        project = s.get("project")
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        out = {k: v for k, v in s.items() if k != "project"}
        out["project"] = {
            "id": project.id,
            "code": project.code,
            "project_code": project.project_code or project.code,
            "name": project.name,
            "city": project.city,
            "location": project.location or project.city,
            "contract_total": float(project.contract_total or 0),
            "contract_amount": float(project.contract_amount or project.contract_total or 0),
        }
        return out
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("project summary query failed: pid=%s", pid)
        raise
    finally:
        db.close()


@router.get("/api/projects/{pid}/matching")
def api_matching(pid: int, _user=_reader_dependency):
    db = SessionLocal()
    try:
        if db.get(Project, pid) is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        rows = matching_rows(db, pid)
        # 序列化 Decimal → float
        for r in rows:
            for k in ("contract", "fulfillment", "invoice", "paid"):
                r[k] = float(r[k])
        return rows
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("matching query failed: pid=%s", pid)
        raise
    finally:
        db.close()


@router.get("/api/projects/{pid}/matching/completeness")
def api_matching_completeness(pid: int, _user=_reader_dependency):
    """Return deterministic four-flow evidence completeness for one project.

    The legacy ``/matching`` response remains a list for compatibility.  This
    aggregate endpoint is intentionally separate because a JSON list cannot
    carry aggregate metadata without breaking existing callers.  ``score`` is
    null: the only published metric is ``percentage``, which is explicitly
    evidence completeness and is not a project-health score.
    """
    db = SessionLocal()
    try:
        if db.get(Project, pid) is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        rows = matching_rows(db, pid)
        return four_flow_evidence_completeness(rows)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("matching completeness query failed: pid=%s", pid)
        raise
    finally:
        db.close()
