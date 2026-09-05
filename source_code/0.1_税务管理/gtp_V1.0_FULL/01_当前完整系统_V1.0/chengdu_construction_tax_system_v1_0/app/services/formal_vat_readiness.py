"""Read-only diagnostics for Formal VAT statutory readiness.

The diagnostics never weaken the fail-closed statutory boundary. They expose
why an official VAT ledger is absent and provide explicitly non-statutory fact
observations so callers do not confuse "not generated" with a formal zero.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from .formal_vat_rebuild import FormalVatRebuildBlockedError, make_formal_vat_rebuild_plan
from .formal_vat_statutory import (
    FormalVatStatutoryResourceIntegrityError,
    FormalVatStatutoryResourceNotFoundError,
    get_formal_vat_statutory_resource,
)
from .legal_entity_fact_periods import LegalEntityNotFoundError


def _period(value: str) -> date:
    normalized = str(value or "").strip()
    try:
        parsed = date.fromisoformat(f"{normalized}-01")
    except ValueError as exc:
        raise ValueError("period must be YYYY-MM") from exc
    if parsed.strftime("%Y-%m") != normalized:
        raise ValueError("period must be YYYY-MM")
    return parsed


def _money(value: Any) -> float:
    try:
        return float(Decimal(str(value or 0)))
    except Exception:
        return 0.0


def _resolve_party_id(db, entity_code: str) -> tuple[str, int]:
    wanted = str(entity_code or "").strip().upper()
    row = db.execute(
        text(
            "SELECT UPPER(ie.canonical_code) AS entity_code, p.id AS party_id "
            "FROM parties p "
            "JOIN internal_entities ie ON ie.party_id = p.id "
            "WHERE p.party_type = 'internal' AND p.active = TRUE "
            "AND ie.active = TRUE AND ie.legal_entity = TRUE "
            "AND UPPER(ie.canonical_code) = :entity_code LIMIT 1"
        ),
        {"entity_code": wanted},
    ).mappings().one_or_none()
    if row is None:
        raise LegalEntityNotFoundError(wanted)
    return str(row["entity_code"]), int(row["party_id"])


def _fact_preview(db, *, entity_code: str, party_id: int, tax_period: date) -> dict[str, Any]:
    period_label = tax_period.strftime("%Y-%m")
    invoice_count = int(
        db.execute(
            text(
                "SELECT COUNT(*) FROM analytics_canonical_facts_current "
                "WHERE fact_type = 'invoice' "
                "AND left(btrim(COALESCE(NULLIF(payload::jsonb ->> 'invoice_date',''), payload::jsonb ->> 'period','')), 7) = :period "
                "AND ("
                "UPPER(btrim(COALESCE(NULLIF(payload::jsonb ->> 'seller_entity_code',''), payload::jsonb ->> 'seller_code',''))) = :entity_code "
                "OR UPPER(btrim(COALESCE(NULLIF(payload::jsonb ->> 'buyer_entity_code',''), payload::jsonb ->> 'buyer_code',''))) = :entity_code"
                ")"
            ),
            {"period": period_label, "entity_code": entity_code},
        ).scalar_one()
        or 0
    )
    output = db.execute(
        text(
            "SELECT COUNT(*) AS total_count, "
            "COUNT(*) FILTER (WHERE event_status='CONFIRMED') AS confirmed_count, "
            "COUNT(*) FILTER (WHERE event_status='NEEDS_REVIEW') AS review_count, "
            "COALESCE(SUM(vat_amount) FILTER (WHERE event_status='CONFIRMED'),0) AS confirmed_amount "
            "FROM output_vat_events WHERE reporting_party_id=:party_id AND output_vat_period=:period"
        ),
        {"party_id": party_id, "period": tax_period},
    ).mappings().one()
    input_claim = db.execute(
        text(
            "SELECT COUNT(*) AS total_count, "
            "COUNT(*) FILTER (WHERE claim_status='CONFIRMED') AS confirmed_count, "
            "COUNT(*) FILTER (WHERE claim_status='NEEDS_REVIEW') AS review_count, "
            "COALESCE(SUM(claim_amount) FILTER (WHERE claim_status='CONFIRMED'),0) AS confirmed_amount "
            "FROM input_vat_claims WHERE reporting_party_id=:party_id AND claim_period=:period"
        ),
        {"party_id": party_id, "period": tax_period},
    ).mappings().one()
    prepayment = db.execute(
        text(
            "SELECT COUNT(*) AS total_count, COALESCE(SUM(tpf.tax_amount),0) AS amount "
            "FROM tax_prepayment_facts tpf JOIN facts f ON f.id=tpf.fact_id "
            "WHERE tpf.reporting_party_id=:party_id AND tpf.tax_type='VAT' "
            "AND tpf.tax_period=:period AND f.is_current=TRUE AND f.validation_status='VALID'"
        ),
        {"party_id": party_id, "period": tax_period},
    ).mappings().one()
    return {
        "invoice_fact_count": invoice_count,
        "output_event_count": int(output["total_count"] or 0),
        "confirmed_output_event_count": int(output["confirmed_count"] or 0),
        "output_needs_review_count": int(output["review_count"] or 0),
        "observed_output_vat": _money(output["confirmed_amount"]),
        "input_claim_count": int(input_claim["total_count"] or 0),
        "confirmed_input_claim_count": int(input_claim["confirmed_count"] or 0),
        "input_needs_review_count": int(input_claim["review_count"] or 0),
        "observed_input_vat": _money(input_claim["confirmed_amount"]),
        "prepayment_count": int(prepayment["total_count"] or 0),
        "observed_prepayment": _money(prepayment["amount"]),
        "has_business_facts": bool(
            invoice_count
            or output["total_count"]
            or input_claim["total_count"]
            or prepayment["total_count"]
        ),
    }


def _reason_code(detail: str) -> str:
    value = detail.lower()
    if "unresolved vat evidence" in value:
        return "EVIDENCE_REVIEW_REQUIRED"
    if "reviewed output vat completeness assertion is required" in value:
        return "OUTPUT_ASSERTION_REQUIRED"
    if "confirmed output vat events do not match" in value:
        return "OUTPUT_ASSERTION_MISMATCH"
    if "no prior vat period and no reviewed opening-balance seed" in value:
        return "OPENING_BALANCE_REQUIRED"
    if "prior vat period exists but has no official statutory resource" in value:
        return "PRIOR_PERIOD_REQUIRED"
    if "prior vat statutory resource is inconsistent" in value:
        return "PRIOR_PERIOD_INVALID"
    if "reviewed opening seed conflicts" in value:
        return "OPENING_BALANCE_CONFLICT"
    return "FORMAL_VAT_REBUILD_BLOCKED"


def get_formal_vat_readiness(db, entity_code: str, period: str) -> dict[str, Any]:
    """Return a machine-readable, read-only readiness snapshot for one month."""
    tax_period = _period(period)
    wanted, party_id = _resolve_party_id(db, entity_code)
    preview = _fact_preview(db, entity_code=wanted, party_id=party_id, tax_period=tax_period)

    try:
        official = get_formal_vat_statutory_resource(db, wanted, period)
        return {
            "status": "READY",
            "entity_code": wanted,
            "reporting_party_id": party_id,
            "period": period,
            "formal_status": "FORMAL_READY",
            "formal_resource_exists": True,
            "rebuild_eligible": False,
            "blocking_reasons": [],
            "facts": preview,
            "formal_resource": {
                "ledger_id": official["vat_ledger"]["id"],
                "calculation_run_id": official["calculation_run"]["id"],
            },
        }
    except FormalVatStatutoryResourceNotFoundError:
        pass
    except FormalVatStatutoryResourceIntegrityError as exc:
        return {
            "status": "READY",
            "entity_code": wanted,
            "reporting_party_id": party_id,
            "period": period,
            "formal_status": "FORMAL_RESOURCE_INVALID",
            "formal_resource_exists": False,
            "rebuild_eligible": False,
            "blocking_reasons": [{"code": "FORMAL_RESOURCE_INVALID", "message": str(exc)}],
            "facts": preview,
        }

    try:
        plan = make_formal_vat_rebuild_plan(db, entity_code=wanted, period=period)
        period_state = plan.get("period_state") or {}
        if period_state.get("restatement_required"):
            return {
                "status": "READY",
                "entity_code": wanted,
                "reporting_party_id": party_id,
                "period": period,
                "formal_status": "CLOSED_RESTATEMENT_REQUIRED",
                "formal_resource_exists": False,
                "rebuild_eligible": False,
                "blocking_reasons": [
                    {"code": "CLOSED_RESTATEMENT_REQUIRED", "message": "目标 VAT 期间已关闭，需要显式重述流程。"}
                ],
                "facts": preview,
            }
        return {
            "status": "READY",
            "entity_code": wanted,
            "reporting_party_id": party_id,
            "period": period,
            "formal_status": "READY_TO_BUILD",
            "formal_resource_exists": False,
            "rebuild_eligible": True,
            "blocking_reasons": [],
            "facts": preview,
        }
    except FormalVatRebuildBlockedError as exc:
        code = _reason_code(str(exc))
        status = "ZERO_ACTIVITY_UNVERIFIED" if not preview["has_business_facts"] else code
        return {
            "status": "READY",
            "entity_code": wanted,
            "reporting_party_id": party_id,
            "period": period,
            "formal_status": status,
            "formal_resource_exists": False,
            "rebuild_eligible": False,
            "blocking_reasons": [{"code": code, "message": str(exc)}],
            "facts": preview,
        }


__all__ = ["get_formal_vat_readiness"]
