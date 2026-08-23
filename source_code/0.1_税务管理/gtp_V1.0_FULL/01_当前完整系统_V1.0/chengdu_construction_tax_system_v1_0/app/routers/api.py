"""V0.2: JSON API 路由。"""
from __future__ import annotations

from fastapi import APIRouter

from ..calc import matching_rows, project_summary
from ..db import SessionLocal

router = APIRouter()


@router.get("/api/projects/{pid}")
def api_project(pid: int):
    db = SessionLocal()
    s = project_summary(db, pid)
    out = {k: v for k, v in s.items() if k != "project"}
    out["project"] = {
        "id": s["project"].id,
        "code": s["project"].code,
        "name": s["project"].name,
    }
    db.close()
    return out


@router.get("/api/projects/{pid}/matching")
def api_matching(pid: int):
    db = SessionLocal()
    rows = matching_rows(db, pid)
    db.close()
    # 序列化 Decimal → float
    for r in rows:
        for k in ("contract", "fulfillment", "invoice", "paid"):
            r[k] = float(r[k])
    return rows