"""Explicit Formal VAT completeness review API.

The GET endpoint is read-only. The POST endpoint records an authenticated
operator's explicit confirmation of the currently observed CONFIRMED Output and
Input VAT totals. It never rebuilds the statutory ledger itself; callers must
re-check readiness and then invoke the existing fail-closed rebuild endpoint.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.dependencies import require_role
from app.services.formal_vat_completeness_review import (
    FormalVatCompletenessReviewError,
    get_formal_vat_completeness_review,
    review_formal_vat_completeness,
)
from app.services.legal_entity_fact_periods import LegalEntityNotFoundError

router = APIRouter(prefix="/api/v3", tags=["v3-canonical"])
_writer_dependency = Depends(require_role("admin", "operator"))


class FormalVatCompletenessReviewRequest(BaseModel):
    expected_output_vat_total: Decimal
    expected_input_vat_total: Decimal


def get_completeness_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _actor(user: Any) -> str:
    if isinstance(user, dict):
        for key in ("username", "name", "email", "sub"):
            value = str(user.get(key) or "").strip()
            if value:
                return value[:80]
    for key in ("username", "name", "email"):
        value = str(getattr(user, key, "") or "").strip()
        if value:
            return value[:80]
    return "authenticated-user"


@router.get("/legal-entities/{entity_code}/statutory-vat/completeness")
def legal_entity_statutory_vat_completeness(
    entity_code: str,
    period: str = Query(...),
    db: Session = Depends(get_completeness_db),
):
    try:
        return get_formal_vat_completeness_review(db, entity_code, period)
    except LegalEntityNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "LEGAL_ENTITY_NOT_FOUND", "detail": "未找到有效内部法人主体"},
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Formal VAT 完整性复核数据源暂时不可用") from exc


@router.post("/legal-entities/{entity_code}/statutory-vat/completeness/review")
def review_legal_entity_statutory_vat_completeness(
    entity_code: str,
    payload: FormalVatCompletenessReviewRequest,
    period: str = Query(...),
    db: Session = Depends(get_completeness_db),
    user=_writer_dependency,
):
    try:
        result = review_formal_vat_completeness(
            db,
            entity_code,
            period,
            expected_output_vat_total=payload.expected_output_vat_total,
            expected_input_vat_total=payload.expected_input_vat_total,
            reviewed_by=_actor(user),
        )
        db.commit()
        return result
    except LegalEntityNotFoundError:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "LEGAL_ENTITY_NOT_FOUND", "detail": "未找到有效内部法人主体"},
        ) from None
    except FormalVatCompletenessReviewError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "FORMAL_VAT_COMPLETENESS_REVIEW_REJECTED", "detail": str(exc)},
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Formal VAT 完整性复核数据源暂时不可用") from exc


__all__ = ["router"]
