"""法人月度税务台账路由。

The browser GET is deliberately read-only.  Rebuilding a period is an
explicit, authenticated and CSRF-protected POST operation.
"""
from __future__ import annotations

import hmac
import logging
import os
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from ..auth import CSRF_COOKIE_NAME
from ..calc import rebuild_tax_ledger
from ..db import SessionLocal
from ..dependencies import require_role
from ..models import Entity, Invoice, Project, RealCost, TaxLedger, TaxRule
from ..templates import templates

router = APIRouter()
_LOGGER = logging.getLogger(__name__)
_PERIOD_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_reader_dependency = Depends(require_role("admin", "operator"))
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
    if os.getenv("APP_ENV", "development").lower() not in {"test", "production"}:
        return
    token = str(supplied or "").strip()
    if not token:
        token = request.headers.get("X-CSRF-Token", "").strip()
    if not token:
        # Keep parity with AuthMiddleware's accepted API header alias.
        token = request.headers.get("X-XSRF-TOKEN", "").strip()
    cookie = request.cookies.get(CSRF_COOKIE_NAME, "").strip()
    if not token or not cookie or not hmac.compare_digest(token, cookie):
        raise HTTPException(status_code=403, detail="CSRF token 无效或缺失")


def _serialize_tax_ledger_row(row: TaxLedger, entity_name: str = "") -> dict[str, object]:
    """Serialize the legal-entity ledger without introducing project scope."""
    return {
        "id": int(row.id),
        "period": row.period,
        "entity_code": row.entity_code,
        "entity_name": entity_name,
        "output_vat": float(row.output_vat or 0),
        "input_vat": float(row.input_vat or 0),
        "vat_payable": float(row.vat_payable or 0),
        "revenue": float(row.revenue or 0),
        "real_cost": float(row.real_cost or 0),
        "estimated_profit": float(row.estimated_profit or 0),
        "estimated_cit": float(row.estimated_cit or 0),
        "cit_note": row.cit_note or "",
        "generated": bool(row.generated),
    }


def _read_entity_ledger(
    db,
    period: str,
    *,
    entity_codes: set[str] | None = None,
) -> list[dict[str, object]]:
    """Read legal-entity TaxLedger rows for one period.

    ``entity_codes`` is only used by the deprecated compatibility endpoint to
    restrict its legacy project filter to entities actually touched by that
    project.  The canonical entity-ledger endpoint never accepts project scope.
    """
    if entity_codes is not None and not entity_codes:
        return []

    stmt = (
        select(TaxLedger, Entity.name)
        .join(Entity, Entity.code == TaxLedger.entity_code)
        .where(
            TaxLedger.period == period,
            Entity.legal_entity.is_(True),
        )
        .order_by(TaxLedger.entity_code, TaxLedger.id)
    )
    if entity_codes is not None:
        stmt = stmt.where(TaxLedger.entity_code.in_(sorted(entity_codes)))

    rows = db.execute(stmt).all()
    return [
        _serialize_tax_ledger_row(ledger, str(entity_name or ""))
        for ledger, entity_name in rows
    ]


def _project_entity_codes(db, project_id: int) -> set[str]:
    """Return internal entity codes actually referenced by project evidence."""
    invoice_codes = db.execute(
        select(Invoice.entity_code)
        .where(Invoice.project_id == project_id)
        .distinct()
    ).scalars().all()
    cost_codes = db.execute(
        select(RealCost.entity_code)
        .where(RealCost.project_id == project_id)
        .distinct()
    ).scalars().all()
    return {
        str(code).strip()
        for code in [*invoice_codes, *cost_codes]
        if str(code or "").strip()
    }


@router.get("/api/entity-tax-ledger")
def api_entity_tax_ledger(
    period: str = Query(default="2026-08", min_length=7, max_length=7),
    project_id: int | None = Query(default=None, include_in_schema=False),
    _user=_reader_dependency,
):
    """Return the legal-entity monthly tax ledger.

    Project scope is deliberately forbidden.  Legal-entity tax positions are
    authoritative at entity × period scope and must never be derived from a
    single project's evidence set.
    """
    if project_id is not None:
        raise HTTPException(
            status_code=422,
            detail="entity-tax-ledger 不接受 project_id；请使用 /api/project-tax-analysis",
        )

    requested_period = _validate_period(period)
    db = SessionLocal()
    try:
        items = _read_entity_ledger(db, requested_period)
        return {
            "status": "READY",
            "period": requested_period,
            "items": items,
            "total": len(items),
            "message": "" if items else "该期间尚无已生成的法人税务台账。",
        }
    except SQLAlchemyError as exc:
        db.rollback()
        _LOGGER.exception("entity tax ledger read failed: period=%s", requested_period)
        raise HTTPException(status_code=503, detail="法人税务台账数据源暂时不可用") from exc
    finally:
        db.close()


@router.get("/api/project-tax-analysis")
def api_project_tax_analysis(
    project_id: int = Query(..., ge=1),
    period: str = Query(default="2026-08", min_length=7, max_length=7),
    _user=_reader_dependency,
):
    """Return a project-only VAT/cost picture for one accounting period.

    This endpoint intentionally never reads ``TaxLedger``.  Project analysis
    is derived only from evidence rows carrying this exact ``project_id`` so a
    legal entity's activity on another project cannot bleed into the result.
    """
    requested_period = _validate_period(period)
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")

        invoice_totals = db.execute(
            select(
                func.coalesce(
                    func.sum(case((Invoice.direction == "out", Invoice.net), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((Invoice.direction == "out", Invoice.vat), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((Invoice.direction == "in", Invoice.net), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((Invoice.direction == "in", Invoice.vat), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (Invoice.direction == "in")
                                & (Invoice.deductible.is_(True)),
                                Invoice.vat,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.count(Invoice.id),
            ).where(
                Invoice.project_id == project_id,
                Invoice.period == requested_period,
            )
        ).one()

        real_cost = db.scalar(
            select(func.coalesce(func.sum(RealCost.amount), 0)).where(
                RealCost.project_id == project_id,
                RealCost.period == requested_period,
            )
        ) or 0

        item = {
            "project_id": int(project.id),
            "project_code": project.project_code or project.code,
            "project_name": project.name,
            "period": requested_period,
            "out_invoice_net": float(invoice_totals[0] or 0),
            "out_invoice_vat": float(invoice_totals[1] or 0),
            "in_invoice_net": float(invoice_totals[2] or 0),
            "in_invoice_vat": float(invoice_totals[3] or 0),
            "deductible_input_vat": float(invoice_totals[4] or 0),
            "real_cost": float(real_cost),
            "invoice_count": int(invoice_totals[5] or 0),
        }
        return {
            "status": "READY",
            "period": requested_period,
            "items": [item],
            "total": 1,
        }
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        _LOGGER.exception(
            "project tax analysis failed: project_id=%s period=%s",
            project_id,
            requested_period,
        )
        raise HTTPException(status_code=503, detail="项目税务分析数据源暂时不可用") from exc
    finally:
        db.close()


@router.get("/api/tax-ledger", deprecated=True)
def api_tax_ledger_legacy(
    period: str = Query(default="2026-08", min_length=7, max_length=7),
    project_id: int | None = Query(default=None, ge=1),
    _user=_reader_dependency,
):
    """Deprecated compatibility reader for the pre-V3 mixed-scope endpoint."""
    requested_period = _validate_period(period)
    db = SessionLocal()
    try:
        entity_codes: set[str] | None = None
        if project_id is not None:
            if db.get(Project, project_id) is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            entity_codes = _project_entity_codes(db, project_id)

        items = _read_entity_ledger(
            db,
            requested_period,
            entity_codes=entity_codes,
        )
        return {
            "status": "READY",
            "period": requested_period,
            "project_id": project_id,
            "items": items,
            "total": len(items),
            "deprecated": True,
            "deprecation_message": (
                "/api/tax-ledger 已弃用：法人月度台账请使用 /api/entity-tax-ledger；"
                "项目经营税务分析请使用 /api/project-tax-analysis。"
            ),
            "recommended_endpoints": {
                "entity_ledger": "/api/entity-tax-ledger",
                "project_analysis": "/api/project-tax-analysis",
            },
        }
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        _LOGGER.exception(
            "legacy tax ledger read failed: project_id=%s period=%s",
            project_id,
            requested_period,
        )
        raise HTTPException(status_code=503, detail="税务台账数据源暂时不可用") from exc
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
