"""Phase 3 Canonical-vs-legacy reconciliation endpoints."""
from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..services.ssot_reconciliation import reconcile_project, reconciliation_summary

router = APIRouter(prefix="/api/v1/canonical-ssot/reconciliation", tags=["canonical-ssot"])


def get_reconciliation_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/projects/{project_id}")
def project_reconciliation(
    project_id: int,
    db: Session = Depends(get_reconciliation_db),
):
    try:
        return reconcile_project(db, project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/summary")
def all_projects_reconciliation(db: Session = Depends(get_reconciliation_db)):
    return reconciliation_summary(db)


__all__ = ["router"]
