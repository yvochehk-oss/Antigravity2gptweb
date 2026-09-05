"""Read-only diagnostics for Formal VAT statutory readiness.

The diagnostics never weaken the fail-closed statutory boundary. Confirmed/current
business and tax observations remain FACT data sourced from RAG PostgreSQL even
when the official VAT ledger is blocked or absent.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.domain.tax_data_policy import DATA_CLASS_FACT, FACT_SOURCE_RAG_POSTGRESQL

from .formal_vat_rag_facts import load_rag_vat_observation
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
    rag = load_rag_vat_observation(db, entity_code, tax_period)
    output_review_count = int(
        db.execute(
            text(
                "SELECT COUNT(*) FROM output_vat_events "
                "WHERE reporting_party_id=:party_id AND output_vat_period=:period "
                "AND event_status='NEEDS_REVIEW'"
            ),
            {"party_id": party_id, "period": tax_period},
        ).scalar_one()
        or 0
    )
    input_review_count = int(
        db.execute(
            text(
                "SELECT COUNT(*) FROM input_vat_claims "
                "WHERE reporting_party_id=:party_id AND claim_period=:period "
                "AND claim_status='NEEDS_REVIEW'"
            ),
            {"party_id": party_id, "period": tax_period},
        ).scalar_one()
        or 0
    )
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
        "data_class": DATA_CLASS_FACT,
        "source": FACT_SOURCE_RAG_POSTGRESQL,
        "actual_occurred": True,
        "is_filing_basis": False,
        "invoice_fact_count": int(rag["invoice_fact_count"]),
        "output_event_count": int(rag["output_fact_count"]),
        "confirmed_output_event_count": int(rag["output_fact_count"]),
        "output_needs_review_count": output_review_count,
        "observed_output_vat": _money(rag["output_vat_total"]),
        "input_claim_count": int(rag["input_fact_count"]),
        "confirmed_input_claim_count": int(rag["input_fact_count"]),
        "input_needs_review_count": input_review_count,
        "observed_input_vat": _money(rag["input_vat_total"]),
        "prepayment_count": int(prepayment["total_count"] or 0),
        "observed_prepayment": _money(prepayment["amount"]),
        "rag_snapshot_sha256": rag["snapshot_sha256"],
        "has_business_facts": bool(
            rag["invoice_fact_count"]
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
    if "reviewed input vat completeness assertion is required" in value:
        return "INPUT_ASSERTION_REQUIRED"
    if "confirmed input vat claims do not match" in value:
        return "INPUT_ASSERTION_MISMATCH"
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
