"""Phase 3/4 V3 compatibility API backed by Canonical Facts."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.dependencies import require_role
from app.services.formal_vat_rebuild import (
    FormalVatRebuildBlockedError,
    rebuild_formal_vat_statutory_resource,
)
from app.services.formal_vat_statutory import (
    FormalVatStatutoryResourceIntegrityError,
    FormalVatStatutoryResourceNotFoundError,
    get_formal_vat_statutory_resource,
)
from app.services.legal_entity_fact_periods import (
    LegalEntityNotFoundError,
    get_legal_entity_fact_periods,
)
from app.services.legal_entity_master_data import list_legal_entities
from app.services.v3_boss_service import V3BossReadError, V3BossService

router = APIRouter(prefix="/api/v3", tags=["v3-canonical"])
_writer_dependency = Depends(require_role("admin", "operator"))


def get_v3_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _read_call(callable_):
    try:
        return callable_()
    except V3BossReadError as exc:
        status = 404 if exc.code == "PROJECT_NOT_FOUND" else 409
        raise HTTPException(status, {"code": exc.code, "detail": exc.detail}) from exc
    except ValueError as exc:
        raise HTTPException(409, {"code": "CANONICAL_READ_REJECTED", "detail": str(exc)}) from exc


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


def _period_ok(period: str) -> str:
    value = str(period or "").strip()
    if len(value) != 7 or value[4] != "-" or not value[:4].isdigit() or not value[5:].isdigit():
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")
    month = int(value[5:])
    if month < 1 or month > 12:
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")
    return value


def _money(value: Any) -> float:
    try:
        return float(Decimal(str(value or 0)))
    except Exception:
        return 0.0


@router.get("/legal-entities")
def legal_entities(
    active: bool = Query(default=True),
    legal_entity: bool = Query(default=True),
    db: Session = Depends(get_v3_db),
):
    items = list_legal_entities(db, active=active, legal_entity=legal_entity)
    return {
        "status": "READY",
        "source_of_truth": "parties+internal_entities",
        "items": items,
        "total": len(items),
    }


@router.get("/legal-entities/{entity_code}/fact-periods")
def legal_entity_fact_periods(entity_code: str, db: Session = Depends(get_v3_db)):
    try:
        return get_legal_entity_fact_periods(db, entity_code)
    except LegalEntityNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "LEGAL_ENTITY_NOT_FOUND", "detail": "未找到有效内部法人主体"},
        ) from None


@router.get("/legal-entities/{entity_code}/statutory-vat")
def legal_entity_statutory_vat(
    entity_code: str,
    period: str = Query(...),
    db: Session = Depends(get_v3_db),
):
    requested = _period_ok(period)
    try:
        return get_formal_vat_statutory_resource(db, entity_code, requested)
    except LegalEntityNotFoundError:
        raise HTTPException(404, {"code": "LEGAL_ENTITY_NOT_FOUND", "detail": "未找到有效内部法人主体"}) from None
    except FormalVatStatutoryResourceNotFoundError:
        raise HTTPException(
            404,
            {
                "code": "FORMAL_VAT_STATUTORY_RESOURCE_NOT_FOUND",
                "detail": "该法人及期间尚无正式 VAT 法定资源。",
            },
        ) from None
    except FormalVatStatutoryResourceIntegrityError as exc:
        raise HTTPException(
            409,
            {"code": "FORMAL_VAT_STATUTORY_RESOURCE_INVALID", "detail": str(exc)},
        ) from exc


@router.post("/legal-entities/{entity_code}/statutory-vat/rebuild")
def rebuild_legal_entity_statutory_vat(
    entity_code: str,
    period: str = Query(...),
    db: Session = Depends(get_v3_db),
    user=_writer_dependency,
):
    requested = _period_ok(period)
    try:
        result = rebuild_formal_vat_statutory_resource(
            db,
            entity_code=entity_code,
            period=requested,
            created_by=_actor(user),
        )
        db.commit()
        return result
    except LegalEntityNotFoundError:
        db.rollback()
        raise HTTPException(404, {"code": "LEGAL_ENTITY_NOT_FOUND", "detail": "未找到有效内部法人主体"}) from None
    except FormalVatRebuildBlockedError as exc:
        db.rollback()
        raise HTTPException(
            409,
            {"code": "FORMAL_VAT_REBUILD_BLOCKED", "detail": str(exc)},
        ) from exc
    except (ValueError, FormalVatStatutoryResourceIntegrityError) as exc:
        db.rollback()
        raise HTTPException(
            409,
            {"code": "FORMAL_VAT_REBUILD_REJECTED", "detail": str(exc)},
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(503, "Formal VAT 数据源暂时不可用") from exc


def _resource_periods(db: Session, entity_code: str) -> list[str]:
    rows = db.execute(
        text(
            "SELECT to_char(tps.tax_period, 'YYYY-MM') AS period "
            "FROM tax_period_states tps "
            "JOIN parties p ON p.id = tps.reporting_party_id "
            "JOIN internal_entities ie ON ie.party_id = p.id "
            "WHERE tps.tax_type = 'VAT' "
            "AND tps.current_run_id IS NOT NULL "
            "AND UPPER(ie.canonical_code) = UPPER(:entity_code) "
            "ORDER BY tps.tax_period"
        ),
        {"entity_code": entity_code},
    ).scalars().all()
    return [str(item) for item in rows if item]


def _invoice_detail_rows(db: Session, entity_code: str, periods: set[str] | None) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT business_key, payload::jsonb AS payload "
            "FROM analytics_canonical_facts_current "
            "WHERE fact_type = 'invoice' "
            "AND ("
            "UPPER(btrim(COALESCE(NULLIF(payload::jsonb ->> 'seller_entity_code',''), payload::jsonb ->> 'seller_code',''))) = UPPER(:entity_code) "
            "OR UPPER(btrim(COALESCE(NULLIF(payload::jsonb ->> 'buyer_entity_code',''), payload::jsonb ->> 'buyer_code',''))) = UPPER(:entity_code)"
            ") ORDER BY business_key"
        ),
        {"entity_code": entity_code},
    ).mappings().all()
    details: list[dict[str, Any]] = []
    wanted = entity_code.strip().upper()
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        transaction_date = str(payload.get("invoice_date") or payload.get("transaction_date") or payload.get("date") or "")
        fact_period = transaction_date[:7] if len(transaction_date) >= 7 else str(payload.get("period") or "")[:7]
        if periods is not None and fact_period not in periods:
            continue
        seller_code = str(payload.get("seller_entity_code") or payload.get("seller_code") or "").strip().upper()
        buyer_code = str(payload.get("buyer_entity_code") or payload.get("buyer_code") or "").strip().upper()
        is_output = seller_code == wanted
        counterparty = (
            payload.get("buyer_name") or payload.get("buyer_legal_name") or buyer_code
            if is_output
            else payload.get("seller_name") or payload.get("seller_legal_name") or seller_code
        )
        details.append(
            {
                "source_type": "INVOICE",
                "entity_code": wanted,
                "period": fact_period,
                "transaction_date": transaction_date,
                "contract_no": str(payload.get("contract_no") or payload.get("contract_code") or payload.get("contract_number") or ""),
                "invoice_no": str(payload.get("invoice_no") or payload.get("invoice_number") or row["business_key"] or ""),
                "counterparty": str(counterparty or ""),
                "tax_type": "VAT_OUTPUT" if is_output else "VAT_INPUT",
                "tax_amount": _money(payload.get("tax_amount") or payload.get("vat_amount") or payload.get("tax")),
                "gross_amount": _money(payload.get("gross_amount") or payload.get("amount_with_tax") or payload.get("amount")),
            }
        )
    return details


def _payment_detail_rows(db: Session, entity_code: str, periods: set[str] | None) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT period, tax_period, payment_date, transaction_date, receipt_no, tax_type, tax_amount, note "
            "FROM tax_payment_records "
            "WHERE UPPER(entity_code) = UPPER(:entity_code) "
            "ORDER BY COALESCE(payment_date, transaction_date), id"
        ),
        {"entity_code": entity_code},
    ).mappings().all()
    details: list[dict[str, Any]] = []
    for row in rows:
        fact_period = str(row["period"] or row["tax_period"] or "")[:7]
        if periods is not None and fact_period not in periods:
            continue
        details.append(
            {
                "source_type": "TAX_PAYMENT",
                "entity_code": entity_code.strip().upper(),
                "period": fact_period,
                "transaction_date": str(row["payment_date"] or row["transaction_date"] or ""),
                "contract_no": "",
                "invoice_no": "",
                "counterparty": "税务机关",
                "receipt_no": str(row["receipt_no"] or ""),
                "tax_type": str(row["tax_type"] or ""),
                "tax_amount": _money(row["tax_amount"]),
                "gross_amount": 0.0,
                "note": str(row["note"] or ""),
            }
        )
    return details


@router.get("/legal-entities/{entity_code}/statutory-vat/export")
def export_legal_entity_statutory_vat(
    entity_code: str,
    view: str = Query(default="current", pattern="^(current|cumulative)$"),
    period: str | None = Query(default=None),
    db: Session = Depends(get_v3_db),
):
    requested_period = _period_ok(period) if period else None
    master = list_legal_entities(db, active=True, legal_entity=True)
    codes = [item["canonical_code"] for item in master]
    selected = codes if entity_code.strip().upper() == "ALL" else [entity_code.strip().upper()]
    if entity_code.strip().upper() != "ALL" and selected[0] not in codes:
        raise HTTPException(404, {"code": "LEGAL_ENTITY_NOT_FOUND", "detail": "未找到有效内部法人主体"})

    summaries: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for code in selected:
        periods = [requested_period] if view == "current" and requested_period else _resource_periods(db, code)
        if view == "current" and not requested_period:
            periods = periods[-1:] if periods else []
        for item_period in periods:
            try:
                summaries.append(get_formal_vat_statutory_resource(db, code, item_period))
            except FormalVatStatutoryResourceNotFoundError:
                continue
        period_filter = set(periods) if periods else set()
        details.extend(_invoice_detail_rows(db, code, period_filter))
        details.extend(_payment_detail_rows(db, code, period_filter))

    return {
        "status": "READY",
        "scope": entity_code.strip().upper(),
        "view": view,
        "period": requested_period,
        "summary": summaries,
        "details": details,
        "summary_count": len(summaries),
        "detail_count": len(details),
    }


@router.get("/boss/projects/{project_id}/snapshot")
def boss_snapshot(
    project_id: int,
    reporting_party_id: int | None = Query(default=None),
    period: date | None = Query(default=None),
    rag_scope: str = Query(default="whole_project"),
    db: Session = Depends(get_v3_db),
):
    return _read_call(
        lambda: V3BossService(db).snapshot(
            project_id,
            reporting_party_id=reporting_party_id,
            tax_period=period,
            rag_scope=rag_scope,
        )
    )


@router.get("/boss/projects/{project_id}/finance")
def boss_finance(project_id: int, db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).finance(project_id))


@router.get("/boss/projects/{project_id}/four-flow")
def boss_four_flow(project_id: int, db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).four_flow(project_id))


@router.get("/boss/projects/{project_id}/tax")
def boss_tax(
    project_id: int,
    reporting_party_id: int | None = Query(default=None),
    period: date | None = Query(default=None),
    db: Session = Depends(get_v3_db),
):
    return _read_call(
        lambda: V3BossService(db).tax(
            project_id,
            reporting_party_id=reporting_party_id,
            tax_period=period,
        )
    )


@router.get("/boss/projects/{project_id}/accounting-rollforward")
def boss_accounting_rollforward(
    project_id: int,
    period: str = Query(..., description="Natural accounting period: YYYY-MM or YYYY-Q1..Q4"),
    db: Session = Depends(get_v3_db),
):
    return _read_call(lambda: V3BossService(db).accounting_rollforward(project_id, period=period))


@router.get("/boss/projects/{project_id}/evidence-quality")
def boss_evidence_quality(project_id: int, db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).evidence_quality(project_id))


@router.get("/boss/projects/{project_id}/rag-context")
def boss_rag_context(
    project_id: int,
    scope: str = Query(default="whole_project"),
    db: Session = Depends(get_v3_db),
):
    return _read_call(lambda: V3BossService(db).rag_context(project_id, scope=scope))


@router.post("/idp/direct", status_code=410)
def idp_direct_retired(_request: Request):
    raise HTTPException(
        status_code=410,
        detail={
            "code": "LEGACY_V3_WRITER_RETIRED",
            "detail": "Phase 3 已冻结 Tax V3 Fact writer；唯一事实写入边界为 RAG canonical_facts。",
        },
    )


@router.get("/system/status")
def v3_system_status(db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).system_status())


def _coded_domain_http_error(err: Exception) -> HTTPException | None:
    from app.integration.idp_canonical.service import CanonicalIngestRejected
    if isinstance(err, CanonicalIngestRejected):
        return HTTPException(status_code=409, detail={"code": err.code, "detail": str(err)})
    return None
