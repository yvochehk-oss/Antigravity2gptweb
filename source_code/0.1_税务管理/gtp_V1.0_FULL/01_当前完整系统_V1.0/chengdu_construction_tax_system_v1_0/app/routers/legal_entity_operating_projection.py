"""Read-only HTTP exposure for legal-entity V3 read models."""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..dependencies import require_role
from ..services.legal_entity_master_data import list_legal_entities
from ..services.legal_entity_scope import aggregate_legal_entity_scope

router = APIRouter(tags=["collections"])
_LOGGER = logging.getLogger(__name__)
_PERIOD_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_reader_dependency = Depends(require_role("admin", "operator"))


@router.get(
    "/api/v3/legal-entities",
    summary="V3 法人 Master Data（Party SSOT）",
)
def legal_entity_master_data(
    active: bool = Query(default=True),
    legal_entity: bool = Query(default=True),
    _user=_reader_dependency,
) -> dict[str, Any]:
    """Expose legal entities from parties + internal_entities only."""
    db = SessionLocal()
    try:
        items = list_legal_entities(db, active=active, legal_entity=legal_entity)
        return {
            "status": "READY",
            "source_of_truth": "parties+internal_entities",
            "items": items,
            "total": len(items),
        }
    except SQLAlchemyError:
        db.rollback()
        _LOGGER.exception(
            "legal entity master data database failure: active=%s legal_entity=%s",
            active,
            legal_entity,
        )
        raise HTTPException(
            status_code=503,
            detail="法人 Master Data 数据源暂时不可用，请稍后重试。",
        ) from None
    finally:
        db.close()


@router.get(
    "/api/legal-entities/{entity_code}/operating-projection",
    summary="法人经营 Canonical Projection（非申报依据）",
)
def legal_entity_operating_projection(
    entity_code: str = Path(..., min_length=1, max_length=64),
    period: str | None = Query(default=None, min_length=7, max_length=7),
    _user=_reader_dependency,
) -> dict[str, Any]:
    """Expose the deterministic legal-entity operating projection.

    The response is explicitly not a filing basis. Official legal-entity VAT
    remains authoritative in ``entity_vat_ledgers``.
    """
    if period and not _PERIOD_RE.fullmatch(period):
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")

    wanted_entity = entity_code.strip()
    if not wanted_entity:
        raise HTTPException(status_code=422, detail="entity_code 不能为空")

    db = SessionLocal()
    try:
        return aggregate_legal_entity_scope(db, wanted_entity, period=period)
    except SQLAlchemyError:
        db.rollback()
        _LOGGER.exception(
            "legal entity operating projection database failure: entity=%s period=%s",
            wanted_entity,
            period,
        )
        raise HTTPException(
            status_code=503,
            detail="法人经营 Projection 数据源暂时不可用，请稍后重试。",
        ) from None
    finally:
        db.close()


__all__ = ["router"]
