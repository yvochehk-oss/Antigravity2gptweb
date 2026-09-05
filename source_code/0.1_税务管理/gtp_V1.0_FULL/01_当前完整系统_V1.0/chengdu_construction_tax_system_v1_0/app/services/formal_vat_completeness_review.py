"""Explicit operator review for Formal VAT period completeness assertions.

This service intentionally does not weaken the statutory rebuild boundary.
Reading a candidate never writes evidence, and only an authenticated caller of
``review_formal_vat_completeness`` may mark the current confirmed Output/Input
VAT totals as reviewed. The subsequent rebuild still re-validates every source
and assertion fail-closed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text

from app.v3_party_models import InternalEntity, Party
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import OutputVatEvent
from app.v3_vat_review_models import VatInputPeriodAssertion, VatOutputPeriodAssertion

from .legal_entity_fact_periods import LegalEntityNotFoundError

MONEY = Decimal("0.01")
REVIEW_SOURCE = "FORMAL_VAT_COMPLETENESS_REVIEW"


class FormalVatCompletenessReviewError(RuntimeError):
    """Raised when the operator review is stale or unresolved evidence exists."""


def _period(value: str | date) -> date:
    if isinstance(value, date):
        parsed = value
    else:
        normalized = str(value or "").strip()
        if len(normalized) == 7:
            normalized = f"{normalized}-01"
        try:
            parsed = date.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("period must be YYYY-MM") from exc
    if parsed.day != 1:
        raise ValueError("period must be YYYY-MM")
    return parsed


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY)


def _resolve_entity(db, entity_code: str) -> tuple[str, int]:
    wanted = str(entity_code or "").strip().upper()
    row = db.execute(
        select(InternalEntity.canonical_code, InternalEntity.party_id)
        .join(Party, Party.id == InternalEntity.party_id)
        .where(
            Party.party_type == "internal",
            Party.active.is_(True),
            InternalEntity.active.is_(True),
            InternalEntity.legal_entity.is_(True),
            func.upper(InternalEntity.canonical_code) == wanted,
        )
    ).one_or_none()
    if row is None:
        raise LegalEntityNotFoundError(wanted)
    return str(row.canonical_code).upper(), int(row.party_id)


def _lock_scope(db, reporting_party_id: int, tax_period: date) -> None:
    period_key = tax_period.year * 100 + tax_period.month
    db.execute(
        text("SELECT pg_advisory_xact_lock(:party_id, :period_key)"),
        {"party_id": int(reporting_party_id), "period_key": int(period_key)},
    )


def _observed(db, reporting_party_id: int, tax_period: date) -> dict[str, Any]:
    output_total = db.scalar(
        select(func.coalesce(func.sum(OutputVatEvent.vat_amount), 0)).where(
            OutputVatEvent.reporting_party_id == reporting_party_id,
            OutputVatEvent.output_vat_period == tax_period,
            OutputVatEvent.event_status == "CONFIRMED",
        )
    )
    input_total = db.scalar(
        select(func.coalesce(func.sum(InputVatClaim.claim_amount), 0)).where(
            InputVatClaim.reporting_party_id == reporting_party_id,
            InputVatClaim.claim_period == tax_period,
            InputVatClaim.claim_status == "CONFIRMED",
        )
    )
    output_review_count = int(
        db.scalar(
            select(func.count(OutputVatEvent.id)).where(
                OutputVatEvent.reporting_party_id == reporting_party_id,
                OutputVatEvent.output_vat_period == tax_period,
                OutputVatEvent.event_status == "NEEDS_REVIEW",
            )
        )
        or 0
    )
    input_review_count = int(
        db.scalar(
            select(func.count(InputVatClaim.id)).where(
                InputVatClaim.reporting_party_id == reporting_party_id,
                InputVatClaim.claim_period == tax_period,
                InputVatClaim.claim_status == "NEEDS_REVIEW",
            )
        )
        or 0
    )
    return {
        "output_vat_total": _money(output_total),
        "input_vat_total": _money(input_total),
        "output_needs_review_count": output_review_count,
        "input_needs_review_count": input_review_count,
    }


def _assertion_payload(row, amount_attr: str, observed_total: Decimal) -> dict[str, Any]:
    if row is None:
        return {
            "exists": False,
            "reviewed": False,
            "asserted_total": None,
            "matches_observed": False,
            "reviewed_by": None,
            "reviewed_at": None,
        }
    asserted = _money(getattr(row, amount_attr))
    return {
        "exists": True,
        "reviewed": bool(row.reviewed),
        "asserted_total": f"{asserted:.2f}",
        "matches_observed": asserted == observed_total,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }


def get_formal_vat_completeness_review(
    db,
    entity_code: str,
    period: str | date,
) -> dict[str, Any]:
    """Return the exact evidence an operator would review, without writing it."""
    tax_period = _period(period)
    wanted, party_id = _resolve_entity(db, entity_code)
    observed = _observed(db, party_id, tax_period)
    output_assertion = db.scalar(
        select(VatOutputPeriodAssertion).where(
            VatOutputPeriodAssertion.reporting_party_id == party_id,
            VatOutputPeriodAssertion.tax_period == tax_period,
        )
    )
    input_assertion = db.scalar(
        select(VatInputPeriodAssertion).where(
            VatInputPeriodAssertion.reporting_party_id == party_id,
            VatInputPeriodAssertion.tax_period == tax_period,
        )
    )
    output_payload = _assertion_payload(
        output_assertion,
        "asserted_output_vat_total",
        observed["output_vat_total"],
    )
    input_payload = _assertion_payload(
        input_assertion,
        "asserted_input_vat_total",
        observed["input_vat_total"],
    )
    can_review = (
        observed["output_needs_review_count"] == 0
        and observed["input_needs_review_count"] == 0
    )
    assertions_current = (
        output_payload["reviewed"]
        and output_payload["matches_observed"]
        and input_payload["reviewed"]
        and input_payload["matches_observed"]
    )
    return {
        "status": "READY",
        "entity_code": wanted,
        "reporting_party_id": party_id,
        "period": tax_period.strftime("%Y-%m"),
        "can_review": can_review,
        "review_required": not assertions_current,
        "observed": {
            "output_vat_total": f"{observed['output_vat_total']:.2f}",
            "input_vat_total": f"{observed['input_vat_total']:.2f}",
            "output_needs_review_count": observed["output_needs_review_count"],
            "input_needs_review_count": observed["input_needs_review_count"],
        },
        "assertions": {
            "output": output_payload,
            "input": input_payload,
        },
    }


def review_formal_vat_completeness(
    db,
    entity_code: str,
    period: str | date,
    *,
    expected_output_vat_total: Decimal,
    expected_input_vat_total: Decimal,
    reviewed_by: str,
) -> dict[str, Any]:
    """Explicitly review current confirmed totals for one legal-entity/month."""
    tax_period = _period(period)
    wanted, party_id = _resolve_entity(db, entity_code)
    actor = str(reviewed_by or "").strip()[:80]
    if not actor:
        raise ValueError("reviewed_by is required")

    _lock_scope(db, party_id, tax_period)
    observed = _observed(db, party_id, tax_period)
    if observed["output_needs_review_count"] or observed["input_needs_review_count"]:
        raise FormalVatCompletenessReviewError(
            "unresolved VAT evidence must be reviewed before completeness can be confirmed: "
            f"output_needs_review_count={observed['output_needs_review_count']}, "
            f"input_needs_review_count={observed['input_needs_review_count']}"
        )

    expected_output = _money(expected_output_vat_total)
    expected_input = _money(expected_input_vat_total)
    if expected_output != observed["output_vat_total"] or expected_input != observed["input_vat_total"]:
        raise FormalVatCompletenessReviewError(
            "stale VAT completeness review: confirmed totals changed after operator preview"
        )

    now = datetime.now(timezone.utc)
    output_assertion = db.scalar(
        select(VatOutputPeriodAssertion)
        .where(
            VatOutputPeriodAssertion.reporting_party_id == party_id,
            VatOutputPeriodAssertion.tax_period == tax_period,
        )
        .with_for_update()
    )
    if output_assertion is None:
        output_assertion = VatOutputPeriodAssertion(
            reporting_party_id=party_id,
            tax_period=tax_period,
            asserted_output_vat_total=observed["output_vat_total"],
            source=REVIEW_SOURCE,
        )
        db.add(output_assertion)
    output_assertion.asserted_output_vat_total = observed["output_vat_total"]
    output_assertion.source = REVIEW_SOURCE
    output_assertion.reviewed = True
    output_assertion.reviewed_by = actor
    output_assertion.reviewed_at = now
    output_assertion.note = "Operator explicitly confirmed the current CONFIRMED Output VAT total."

    input_assertion = db.scalar(
        select(VatInputPeriodAssertion)
        .where(
            VatInputPeriodAssertion.reporting_party_id == party_id,
            VatInputPeriodAssertion.tax_period == tax_period,
        )
        .with_for_update()
    )
    if input_assertion is None:
        input_assertion = VatInputPeriodAssertion(
            reporting_party_id=party_id,
            tax_period=tax_period,
            asserted_input_vat_total=observed["input_vat_total"],
            source=REVIEW_SOURCE,
        )
        db.add(input_assertion)
    input_assertion.asserted_input_vat_total = observed["input_vat_total"]
    input_assertion.source = REVIEW_SOURCE
    input_assertion.reviewed = True
    input_assertion.reviewed_by = actor
    input_assertion.reviewed_at = now
    input_assertion.note = "Operator explicitly confirmed the current CONFIRMED Input VAT total."

    db.flush()
    return get_formal_vat_completeness_review(db, wanted, tax_period)


__all__ = [
    "FormalVatCompletenessReviewError",
    "get_formal_vat_completeness_review",
    "review_formal_vat_completeness",
]
