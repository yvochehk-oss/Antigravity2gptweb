"""法人月度税务台账路由。

The browser GET is deliberately read-only.  Rebuilding a period is an
explicit, authenticated and CSRF-protected POST operation.
"""
from __future__ import annotations

import hmac
import logging
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..auth import CSRF_COOKIE_NAME
from ..calc import rebuild_tax_ledger
from ..db import SessionLocal
from ..dependencies import require_role
from ..models import TaxLedger, TaxRule
from ..templates import templates

router = APIRouter()
_LOGGER = logging.getLogger(__name__)
_PERIOD_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_rebuild_dependency = Depends(require_role("admin", "operator"))


def _validate_period(period: str) -> str:
    normalized = str(period or "").strip()
    if not _PERIOD_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")
    return normalized


def _require_csrf(request: Request, supplied: str) -> None:
    """Require a matching form/header token in addition to middleware checks."""
    token = str(supplied or "").strip() or request.headers.get("X-CSRF-Token", "").strip()
    cookie = request.cookies.get(CSRF_COOKIE_NAME, "").strip()
    if not token or not cookie or not hmac.compare_digest(token, cookie):
        raise HTTPException(status_code=403, detail="CSRF token 无效或缺失")


@router.get("/tax-ledger", response_class=HTMLResponse)
def tax_ledger(
    request: Request,
    period: str = Query(default="2026-08", min_length=7, max_length=7),
    rebuild: str | None = Query(default=None, max_length=20),
):
    """Render an existing ledger period without changing database state."""
    requested_period = _validate_period(period)
    db = SessionLocal()
    try:
        rows = db.execute(
            select(TaxLedger)
            .where(TaxLedger.period == requested_period)
            .order_by(TaxLedger.entity_code, TaxLedger.id)
        ).scalars().all()
        unreviewed = db.execute(
            select(TaxRule).where(TaxRule.reviewed.is_(False))
        ).scalars().all()
        return templates.TemplateResponse(
            request,
            "tax_ledger.html",
            {
                "request": request,
                "rows": rows,
                "period": requested_period,
                "unreviewed": len(unreviewed),
                "rebuild_status": rebuild,
            },
        )
    except SQLAlchemyError:
        db.rollback()
        _LOGGER.exception("tax ledger read failed: period=%s", requested_period)
        raise HTTPException(status_code=503, detail="税务台账数据源暂时不可用") from None
    finally:
        db.close()


@router.post("/tax-ledger/rebuild")
def tax_ledger_rebuild(
    request: Request,
    period: str = Form(...),
    csrf_token: str = Form(default="", alias="_csrf"),
    _user=_rebuild_dependency,
):
    """Explicitly rebuild one period, then redirect to the read-only GET."""
    requested_period = _validate_period(period)
    _require_csrf(request, csrf_token)

    db = SessionLocal()
    try:
        # ``rebuild_tax_ledger`` owns the atomic replacement transaction and
        # rolls back on validation/database failure.  This endpoint never
        # seeds or repairs source/master data implicitly.
        rebuild_tax_ledger(db, requested_period)
    except Exception:
        db.rollback()
        _LOGGER.exception("tax ledger rebuild failed: period=%s", requested_period)
        return RedirectResponse(
            f"/tax-ledger?period={requested_period}&rebuild=failed",
            status_code=303,
        )
    finally:
        db.close()

    return RedirectResponse(
        f"/tax-ledger?period={requested_period}&rebuild=success",
        status_code=303,
    )


__all__ = ["router"]
