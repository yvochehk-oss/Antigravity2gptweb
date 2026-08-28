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
from pydantic import BaseModel, ConfigDict, Field
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


class TaxLedgerRebuildRequest(BaseModel):
    """Explicit JSON command input for rebuilding one accounting period."""

    model_config = ConfigDict(extra="forbid")

    period: str = Field(
        ...,
        min_length=7,
        max_length=7,
        pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$",
        description="Accounting period in YYYY-MM format",
    )


class TaxLedgerRebuildResponse(BaseModel):
    """Stable success contract for the explicit rebuild command."""

    status: str = Field(..., pattern=r"^success$")
    period: str = Field(..., pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    row_count: int = Field(..., ge=0)


def _validate_period(period: str) -> str:
    normalized = str(period or "").strip()
    if not _PERIOD_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")
    return normalized


def _require_csrf(request: Request, supplied: str) -> None:
    """Require a matching form/header token in addition to middleware checks."""
    token = str(supplied or "").strip()
    if not token:
        token = request.headers.get("X-CSRF-Token", "").strip()
    if not token:
        # Keep parity with AuthMiddleware's accepted API header alias.
        token = request.headers.get("X-XSRF-TOKEN", "").strip()
    cookie = request.cookies.get(CSRF_COOKIE_NAME, "").strip()
    if not token or not cookie or not hmac.compare_digest(token, cookie):
        raise HTTPException(status_code=403, detail="CSRF token 无效或缺失")


@router.post(
    "/api/tax-ledger/rebuild",
    response_model=TaxLedgerRebuildResponse,
    summary="显式重建指定期间的确定性税务台账",
)
def api_tax_ledger_rebuild(
    request: Request,
    body: TaxLedgerRebuildRequest,
    _user=_rebuild_dependency,
) -> TaxLedgerRebuildResponse:
    """Rebuild exactly one requested period for the React JSON client.

    The command is deliberately separate from the GET collection endpoint:
    reads never generate or delete ledger rows.  The calculation engine owns
    the source-of-truth transaction; this boundary only validates access,
    maps errors to explicit HTTP statuses, and reports the returned row count.
    """
    # The middleware validates browser origin and any supplied CSRF header.
    # Require the endpoint-level cookie/header match as well so an API JSON
    # request cannot mutate state merely by being same-origin authenticated.
    _require_csrf(request, "")
    requested_period = _validate_period(body.period)

    db = SessionLocal()
    try:
        rows = rebuild_tax_ledger(db, requested_period)
        row_count = len(rows)
    except ValueError as exc:
        # Domain/input/rule failures are client-visible 4xx errors.  The
        # calculation engine rolls back itself; this boundary also rolls back
        # so a patched or future engine cannot leave this session dirty.
        db.rollback()
        _LOGGER.warning(
            "tax ledger rebuild rejected: period=%s error=%s",
            requested_period,
            str(exc),
        )
        raise HTTPException(status_code=422, detail="税务台账输入或税务规则无效") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        _LOGGER.exception("tax ledger rebuild database failure: period=%s", requested_period)
        raise HTTPException(status_code=503, detail="税务台账数据源暂时不可用") from exc
    except Exception as exc:
        db.rollback()
        _LOGGER.exception("tax ledger rebuild failed: period=%s", requested_period)
        raise HTTPException(status_code=500, detail="税务台账重建失败") from exc
    finally:
        db.close()

    return TaxLedgerRebuildResponse(
        status="success",
        period=requested_period,
        row_count=row_count,
    )


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
