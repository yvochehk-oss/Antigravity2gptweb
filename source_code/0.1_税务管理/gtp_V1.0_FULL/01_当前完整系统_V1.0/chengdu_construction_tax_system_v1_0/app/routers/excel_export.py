"""Read-only multi-sheet Excel export endpoint."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..dependencies import require_role
from ..services.excel_export_service import (
    XLSX_MEDIA_TYPE,
    build_excel_export,
    content_disposition,
)

router = APIRouter(prefix="/api/v3/export", tags=["v3-export"])
_LOGGER = logging.getLogger(__name__)
_READER = Depends(require_role("admin", "operator"))
_PERIOD_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


@router.get("/excel", summary="全系统多 Sheet Excel 导出")
def export_excel(
    scope: str = Query(default="ALL", min_length=1, max_length=64),
    view: str = Query(default="current", pattern="^(current|cumulative)$"),
    period: str | None = Query(default=None, min_length=7, max_length=7),
    _user=_READER,
) -> Response:
    """Download the canonical/statutory workbook without mutating any source data."""
    normalized_scope = scope.strip().upper()
    if not normalized_scope:
        raise HTTPException(status_code=422, detail="scope 不能为空")
    if period and not _PERIOD_RE.fullmatch(period):
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")
    if view == "current" and not period:
        raise HTTPException(status_code=422, detail="current 视图必须指定 period=YYYY-MM")

    db = SessionLocal()
    try:
        artifact = build_excel_export(
            db,
            scope=normalized_scope,
            view=view,
            period=period,
        )
        return Response(
            content=artifact.content,
            media_type=XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": content_disposition(artifact.filename),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except ValueError as exc:
        db.rollback()
        detail = str(exc)
        status = 404 if detail.startswith("unknown legal entity scope:") else 422
        raise HTTPException(status_code=status, detail=detail) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        _LOGGER.exception(
            "Excel export database failure: scope=%s view=%s period=%s",
            normalized_scope,
            view,
            period,
        )
        raise HTTPException(status_code=503, detail="Excel 导出数据源暂时不可用") from exc
    finally:
        db.close()


__all__ = ["router"]
