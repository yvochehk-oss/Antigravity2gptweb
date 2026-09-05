"""Explicit Formal VAT completeness review API.

GET previews are read-only. POST endpoints record an authenticated operator's
explicit confirmation of current Canonical RAG invoice VAT snapshots. They do
not weaken or bypass the existing Formal VAT rebuild gate.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.dependencies import require_role
from app.services.formal_vat_completeness_review import (
    FormalVatCompletenessReviewError,
    get_formal_vat_completeness_review,
    list_formal_vat_completeness_reviews,
    review_formal_vat_completeness,
    review_formal_vat_completeness_bulk,
)
from app.services.legal_entity_fact_periods import LegalEntityNotFoundError

router = APIRouter(prefix="/api/v3", tags=["v3-canonical"])
_writer_dependency = Depends(require_role("admin", "operator"))


class FormalVatCompletenessReviewRequest(BaseModel):
    expected_output_vat_total: Decimal
    expected_input_vat_total: Decimal
    expected_snapshot_sha256: str = Field(min_length=64, max_length=64)
    opening_input_credit: Decimal | None = None


class FormalVatCompletenessBulkItem(FormalVatCompletenessReviewRequest):
    entity_code: str = Field(min_length=1, max_length=16)
    period: str = Field(min_length=7, max_length=7)


class FormalVatCompletenessBulkReviewRequest(BaseModel):
    items: list[FormalVatCompletenessBulkItem] = Field(min_length=1, max_length=500)


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


@router.get("/statutory-vat/completeness/bulk")
def statutory_vat_completeness_bulk(
    entity_code: str = Query(default="ALL"),
    db: Session = Depends(get_completeness_db),
):
    try:
        items = list_formal_vat_completeness_reviews(db, entity_code)
        return {
            "status": "READY",
            "scope": entity_code.strip().upper() or "ALL",
            "source_of_truth": "analytics_canonical_facts_current",
            "items": items,
            "total": len(items),
            "review_required_count": sum(1 for item in items if item["review_required"]),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Formal VAT 批量完整性复核数据源暂时不可用") from exc


@router.post("/statutory-vat/completeness/bulk/review")
def review_statutory_vat_completeness_bulk(
    payload: FormalVatCompletenessBulkReviewRequest,
    db: Session = Depends(get_completeness_db),
    user=_writer_dependency,
):
    try:
        items = [item.model_dump() for item in payload.items]
        results = review_formal_vat_completeness_bulk(
            db,
            items,
            reviewed_by=_actor(user),
        )
        db.commit()
        return {
            "status": "REVIEWED",
            "source_of_truth": "analytics_canonical_facts_current",
            "items": results,
            "total": len(results),
        }
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
        raise HTTPException(status_code=503, detail="Formal VAT 批量完整性复核数据源暂时不可用") from exc


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
            expected_snapshot_sha256=payload.expected_snapshot_sha256,
            opening_input_credit=payload.opening_input_credit,
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
