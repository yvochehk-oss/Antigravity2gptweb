"""Phase 3/4 V3 compatibility API backed by Canonical Facts."""
from __future__ import annotations

from datetime import date
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.v3_boss_service import V3BossReadError, V3BossService

router = APIRouter(prefix="/api/v3", tags=["v3-canonical"])


def get_v3_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _read_call(callable_):
    try:
        return callable_()
    except V3BossReadError as exc:
        status = 404 if exc.code == "PROJECT_NOT_FOUND" else 409
        raise HTTPException(status, {"code": exc.code, "detail": exc.detail}) from exc
    except ValueError as exc:
        raise HTTPException(409, {"code": "CANONICAL_READ_REJECTED", "detail": str(exc)}) from exc


@router.get("/boss/projects/{project_id}/snapshot")
def boss_snapshot(
    project_id: int,
    reporting_party_id: int | None = Query(default=None),
    period: date | None = Query(default=None),
    rag_scope: str = Query(default="whole_project"),
    db: Session = Depends(get_v3_db),
):
    return _read_call(
        lambda: V3BossService(db).snapshot(
            project_id,
            reporting_party_id=reporting_party_id,
            tax_period=period,
            rag_scope=rag_scope,
        )
    )


@router.get("/boss/projects/{project_id}/finance")
def boss_finance(project_id: int, db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).finance(project_id))


@router.get("/boss/projects/{project_id}/four-flow")
def boss_four_flow(project_id: int, db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).four_flow(project_id))


@router.get("/boss/projects/{project_id}/tax")
def boss_tax(
    project_id: int,
    reporting_party_id: int | None = Query(default=None),
    period: date | None = Query(default=None),
    db: Session = Depends(get_v3_db),
):
    return _read_call(
        lambda: V3BossService(db).tax(
            project_id,
            reporting_party_id=reporting_party_id,
            tax_period=period,
        )
    )


@router.get("/boss/projects/{project_id}/evidence-quality")
def boss_evidence_quality(project_id: int, db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).evidence_quality(project_id))


@router.get("/boss/projects/{project_id}/rag-context")
def boss_rag_context(
    project_id: int,
    scope: str = Query(default="whole_project"),
    db: Session = Depends(get_v3_db),
):
    return _read_call(lambda: V3BossService(db).rag_context(project_id, scope=scope))


@router.post("/idp/direct", status_code=410)
def idp_direct_retired(_request: Request):
    raise HTTPException(
        status_code=410,
        detail={
            "code": "LEGACY_V3_WRITER_RETIRED",
            "detail": (
                "Phase 3 已冻结 Tax V3 Fact writer；"
                "唯一事实写入边界为 RAG canonical_facts。"
            ),
        },
    )


@router.get("/system/status")
def v3_system_status(db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).system_status())
