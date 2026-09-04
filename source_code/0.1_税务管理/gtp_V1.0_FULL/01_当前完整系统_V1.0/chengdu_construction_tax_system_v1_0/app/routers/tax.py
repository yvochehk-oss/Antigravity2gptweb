"""Tax ledger and risk disposition mutation routes.

Tax-ledger reads remain read-only. Mutations are explicit, authenticated and
CSRF-protected. Risk disposition writes only the durable ``risk_events.resolved``
state and an audit trail; browser-local state is never treated as authoritative.
"""
from __future__ import annotations

import hmac
import logging
import os
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
from ..models import AuditLog, RiskEvent, TaxLedger, TaxRule
from ..templates import templates

router = APIRouter()
_LOGGER = logging.getLogger(__name__)
_PERIOD_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_rebuild_dependency = Depends(require_role("admin", "operator"))
_risk_disposition_dependency = Depends(require_role("admin", "operator"))


class TaxLedgerRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str = Field(
        ...,
        min_length=7,
        max_length=7,
        pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$",
        description="Accounting period in YYYY-MM format",
    )


class TaxLedgerRebuildResponse(BaseModel):
    status: str = Field(..., pattern=r"^success$")
    period: str = Field(..., pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    row_count: int = Field(..., ge=0)


class RiskDispositionRequest(BaseModel):
    """Durable disposition command for one risk event."""

    model_config = ConfigDict(extra="forbid")
    risk_id: int = Field(..., ge=1)
    resolved: bool = True
    note: str = Field(default="", max_length=1000)
    handler: str = Field(default="", max_length=120)


class RiskDispositionResponse(BaseModel):
    status: str = Field(..., pattern=r"^success$")
    risk_id: int
    resolved: bool
    display_status: str


def _validate_period(period: str) -> str:
    normalized = str(period or "").strip()
    if not _PERIOD_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")
    return normalized


def _require_csrf(request: Request, supplied: str) -> None:
    if os.getenv("APP_ENV", "development").lower() not in {"test", "production"}:
        return
    token = str(supplied or "").strip()
    if not token:
        token = request.headers.get("X-CSRF-Token", "").strip()
    if not token:
        token = request.headers.get("X-XSRF-TOKEN", "").strip()
    cookie = request.cookies.get(CSRF_COOKIE_NAME, "").strip()
    if not token or not cookie or not hmac.compare_digest(token, cookie):
        raise HTTPException(status_code=403, detail="CSRF token 无效或缺失")


def _actor_label(user) -> str:
    if user is None:
        return "anonymous"
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


@router.post(
    "/api/v1/risk/disposition",
    response_model=RiskDispositionResponse,
    summary="持久化风险处置状态",
)
def api_risk_disposition(
    request: Request,
    body: RiskDispositionRequest,
    user=_risk_disposition_dependency,
) -> RiskDispositionResponse:
    """Persist a risk close/reopen action and record an audit trail atomically."""
    _require_csrf(request, "")
    db = SessionLocal()
    try:
        event = db.get(RiskEvent, body.risk_id)
        if event is None:
            raise HTTPException(status_code=404, detail="未找到指定风险事件")

        previous = bool(event.resolved)
        event.resolved = bool(body.resolved)
        actor = _actor_label(user)
        action = "RISK_RESOLVED" if body.resolved else "RISK_REOPENED"
        note = body.note.strip()
        handler = body.handler.strip()
        audit_message = (
            f"risk_id={event.id}; project_id={event.project_id}; "
            f"resolved:{previous}->{event.resolved}"
        )
        if handler:
            audit_message += f"; handler={handler}"
        if note:
            audit_message += f"; note={note}"
        db.add(
            AuditLog(
                action=action,
                object_type="risk_event",
                object_id=str(event.id),
                message=audit_message,
                actor=actor,
                ip=str(request.client.host if request.client else ""),
                request_id=str(request.headers.get("X-Request-ID", ""))[:64],
            )
        )
        db.commit()
        db.refresh(event)
        return RiskDispositionResponse(
            status="success",
            risk_id=int(event.id),
            resolved=bool(event.resolved),
            display_status="已闭环" if event.resolved else "待处置",
        )
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        _LOGGER.exception("risk disposition database failure: risk_id=%s", body.risk_id)
        raise HTTPException(status_code=503, detail="风险处置数据源暂时不可用") from exc
    except Exception as exc:
        db.rollback()
        _LOGGER.exception("risk disposition failed: risk_id=%s", body.risk_id)
        raise HTTPException(status_code=500, detail="风险处置失败") from exc
    finally:
        db.close()


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
    _require_csrf(request, "")
    requested_period = _validate_period(body.period)
    db = SessionLocal()
    try:
        rows = rebuild_tax_ledger(db, requested_period)
        row_count = len(rows)
    except ValueError as exc:
        db.rollback()
        _LOGGER.warning(
            "tax ledger rebuild rejected: period=%s error=%s",
            requested_period,
            str(exc),
        )
        raise HTTPException(status_code=422, detail=f"税务台账输入或税务规则无效: {exc}") from exc
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

    return TaxLedgerRebuildResponse(status="success", period=requested_period, row_count=row_count)


@router.get("/tax-ledger", response_class=HTMLResponse)
def tax_ledger(
    request: Request,
    period: str = Query(default="2026-08", min_length=7, max_length=7),
    rebuild: str | None = Query(default=None, max_length=20),
):
    requested_period = _validate_period(period)
    db = SessionLocal()
    try:
        rows = db.execute(
            select(TaxLedger)
            .where(TaxLedger.period == requested_period)
            .order_by(TaxLedger.entity_code, TaxLedger.id)
        ).scalars().all()
        unreviewed = db.execute(select(TaxRule).where(TaxRule.reviewed.is_(False))).scalars().all()
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
    requested_period = _validate_period(period)
    _require_csrf(request, csrf_token)
    db = SessionLocal()
    try:
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
