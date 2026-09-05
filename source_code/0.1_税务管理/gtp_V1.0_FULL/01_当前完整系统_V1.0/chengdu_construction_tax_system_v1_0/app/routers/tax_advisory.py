"""Read-only Tax FACT/ADVISORY API."""
from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.tax_advisory import build_tax_advisory

router = APIRouter(prefix="/api/v3", tags=["v3-tax-advisory"])


def get_tax_advisory_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/projects/{project_id}/tax-advisory")
def project_tax_advisory(
    project_id: int,
    reporting_party_id: int | None = Query(default=None, gt=0),
    period: str | None = Query(default=None),
    db: Session = Depends(get_tax_advisory_db),
):
    try:
        return build_tax_advisory(
            db,
            int(project_id),
            reporting_party_id=reporting_party_id,
            period=period,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "detail": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "TAX_ADVISORY_INVALID_SCOPE", "detail": str(exc)},
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "TAX_ADVISORY_SOURCE_UNAVAILABLE", "detail": "RAG PostgreSQL / Tax advisory data source is unavailable"},
        ) from exc


__all__ = ["router"]
