"""Read-only Formal VAT readiness diagnostics API."""
from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.formal_vat_readiness import get_formal_vat_readiness
from app.services.legal_entity_fact_periods import LegalEntityNotFoundError

router = APIRouter(prefix="/api/v3", tags=["v3-canonical"])


def get_readiness_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/legal-entities/{entity_code}/statutory-vat/readiness")
def legal_entity_statutory_vat_readiness(
    entity_code: str,
    period: str = Query(...),
    db: Session = Depends(get_readiness_db),
):
    try:
        return get_formal_vat_readiness(db, entity_code, period)
    except LegalEntityNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "LEGAL_ENTITY_NOT_FOUND", "detail": "未找到有效内部法人主体"},
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Formal VAT readiness 数据源暂时不可用") from exc
