"""Phase 4 deterministic accounting, CIT and triple-lineage APIs."""
from __future__ import annotations

from decimal import Decimal
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..services.phase4_accounting import (
    build_project_accounting,
    list_accounting_snapshots,
    snapshot_project_accounting,
)

router = APIRouter(prefix="/api/v1/accounting", tags=["canonical-accounting"])


def get_accounting_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _rate(value: float) -> Decimal:
    return Decimal(str(value))


@router.get("/projects/{project_id}/preview")
def accounting_preview(
    project_id: int,
    cit_rate: float = Query(default=0.25, ge=0, le=1),
    db: Session = Depends(get_accounting_db),
):
    try:
        return build_project_accounting(db, project_id, cit_rate=_rate(cit_rate))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/snapshots")
def create_accounting_snapshot(
    project_id: int,
    cit_rate: float = Query(default=0.25, ge=0, le=1),
    db: Session = Depends(get_accounting_db),
):
    try:
        result = snapshot_project_accounting(db, project_id, cit_rate=_rate(cit_rate))
        db.commit()
        return result
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/projects/{project_id}/snapshots")
def accounting_snapshots(
    project_id: int,
    db: Session = Depends(get_accounting_db),
):
    return {
        "project_id": project_id,
        "source_of_truth": "canonical_facts",
        "items": list_accounting_snapshots(db, project_id),
    }


__all__ = ["router"]
