"""Explicit operator review for Formal VAT period completeness assertions.

Canonical invoice facts remain the business source of truth. GET preview is
read-only. Explicit review locks one legal-entity/month, verifies the exact RAG
snapshot, materializes typed statutory-working mirrors, and only then records
reviewed Output/Input completeness assertions. The Formal VAT rebuild continues
to enforce its existing fail-closed source and closed-loop checks.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text

from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import TaxPeriodState
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import OutputVatEvent, VatOpeningBalanceSeed
from app.v3_vat_review_models import VatInputPeriodAssertion, VatOutputPeriodAssertion

from .formal_vat_rag_facts import (
    list_rag_vat_scopes,
    load_rag_vat_observation,
    materialize_rag_vat_evidence,
)
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


def _unresolved_counts(db, reporting_party_id: int, tax_period: date) -> tuple[int, int]:
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
    return output_review_count, input_review_count


def _observed(db, entity_code: str, reporting_party_id: int, tax_period: date) -> dict[str, Any]:
    rag = load_rag_vat_observation(db, entity_code, tax_period)
    output_review_count, input_review_count = _unresolved_counts(
        db,
        reporting_party_id,
        tax_period,
    )
    return {
        "source": rag["source"],
        "entity_code": rag["entity_code"],
        "period": rag["period"],
        "snapshot_sha256": rag["snapshot_sha256"],
        "invoice_fact_count": int(rag["invoice_fact_count"]),
        "output_fact_count": int(rag["output_fact_count"]),
        "input_fact_count": int(rag["input_fact_count"]),
        "output_vat_total": _money(rag["output_vat_total"]),
        "input_vat_total": _money(rag["input_vat_total"]),
        "output_needs_review_count": output_review_count,
        "input_needs_review_count": input_review_count,
        "rows": rag["rows"],
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


def _opening_payload(db, reporting_party_id: int, tax_period: date) -> dict[str, Any]:
    prior_period = date(
        tax_period.year - 1 if tax_period.month == 1 else tax_period.year,
        12 if tax_period.month == 1 else tax_period.month - 1,
        1,
    )
    prior_state = db.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == reporting_party_id,
            TaxPeriodState.tax_type == "VAT",
            TaxPeriodState.tax_period == prior_period,
        )
    )
    seed = db.scalar(
        select(VatOpeningBalanceSeed).where(
            VatOpeningBalanceSeed.reporting_party_id == reporting_party_id,
            VatOpeningBalanceSeed.tax_period == tax_period,
            VatOpeningBalanceSeed.reviewed.is_(True),
        )
    )
    return {
        "required": prior_state is None and seed is None,
        "reviewed_seed_exists": seed is not None,
        "opening_input_credit": f"{Decimal(seed.opening_input_credit):.2f}" if seed is not None else None,
        "prior_period_state_exists": prior_state is not None,
    }


def get_formal_vat_completeness_review(
    db,
    entity_code: str,
    period: str | date,
) -> dict[str, Any]:
    """Return the exact RAG evidence an operator would review, without writing it."""
    tax_period = _period(period)
    wanted, party_id = _resolve_entity(db, entity_code)
    observed = _observed(db, wanted, party_id, tax_period)
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
        "source_of_truth": "analytics_canonical_facts_current",
        "snapshot_sha256": observed["snapshot_sha256"],
        "can_review": can_review,
        "review_required": not assertions_current,
        "opening_balance": _opening_payload(db, party_id, tax_period),
        "observed": {
            "invoice_fact_count": observed["invoice_fact_count"],
            "output_fact_count": observed["output_fact_count"],
            "input_fact_count": observed["input_fact_count"],
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


def _review_opening_seed(
    db,
    *,
    reporting_party_id: int,
    tax_period: date,
    opening_input_credit: Decimal | None,
    actor: str,
    now: datetime,
) -> None:
    if opening_input_credit is None:
        return
    amount = _money(opening_input_credit)
    if amount < 0:
        raise ValueError("opening_input_credit must be non-negative")
    prior = _opening_payload(db, reporting_party_id, tax_period)
    if prior["prior_period_state_exists"]:
        return
    seed = db.scalar(
        select(VatOpeningBalanceSeed)
        .where(
            VatOpeningBalanceSeed.reporting_party_id == reporting_party_id,
            VatOpeningBalanceSeed.tax_period == tax_period,
        )
        .with_for_update()
    )
    if seed is None:
        seed = VatOpeningBalanceSeed(
            reporting_party_id=reporting_party_id,
            tax_period=tax_period,
            opening_input_credit=amount,
            source=REVIEW_SOURCE,
        )
        db.add(seed)
    seed.opening_input_credit = amount
    seed.source = REVIEW_SOURCE
    seed.reviewed = True
    seed.reviewed_by = actor
    seed.reviewed_at = now
    seed.note = "Operator explicitly confirmed the initial Formal VAT opening input credit."


def review_formal_vat_completeness(
    db,
    entity_code: str,
    period: str | date,
    *,
    expected_output_vat_total: Decimal,
    expected_input_vat_total: Decimal,
    reviewed_by: str,
    expected_snapshot_sha256: str | None = None,
    opening_input_credit: Decimal | None = None,
) -> dict[str, Any]:
    """Explicitly review and materialize current RAG invoice VAT for one month."""
    tax_period = _period(period)
    wanted, party_id = _resolve_entity(db, entity_code)
    actor = str(reviewed_by or "").strip()[:80]
    if not actor:
        raise ValueError("reviewed_by is required")

    _lock_scope(db, party_id, tax_period)
    observed = _observed(db, wanted, party_id, tax_period)
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
            "stale VAT completeness review: Canonical RAG totals changed after operator preview"
        )
    if expected_snapshot_sha256 and expected_snapshot_sha256 != observed["snapshot_sha256"]:
        raise FormalVatCompletenessReviewError(
            "stale VAT completeness review: Canonical RAG snapshot changed after operator preview"
        )

    mirror = materialize_rag_vat_evidence(
        db,
        reporting_party_id=party_id,
        entity_code=wanted,
        period=tax_period,
        reviewed_by=actor,
        observation=observed,
    )
    if (
        mirror["output_vat_total"] != observed["output_vat_total"]
        or mirror["input_vat_total"] != observed["input_vat_total"]
    ):
        raise FormalVatCompletenessReviewError(
            "statutory VAT mirror does not converge to the Canonical RAG totals; "
            "non-canonical confirmed VAT rows or duplicate evidence must be reconciled first"
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
    output_assertion.note = (
        "Operator explicitly confirmed the current RAG Canonical Output VAT completeness snapshot "
        f"{observed['snapshot_sha256']}."
    )

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
    input_assertion.note = (
        "Operator explicitly confirmed the current RAG Canonical Input VAT completeness snapshot "
        f"{observed['snapshot_sha256']}."
    )

    _review_opening_seed(
        db,
        reporting_party_id=party_id,
        tax_period=tax_period,
        opening_input_credit=opening_input_credit,
        actor=actor,
        now=now,
    )
    db.flush()
    return get_formal_vat_completeness_review(db, wanted, tax_period)


def list_formal_vat_completeness_reviews(db, entity_code: str | None = None) -> list[dict[str, Any]]:
    return [
        get_formal_vat_completeness_review(db, scope["entity_code"], scope["period"])
        for scope in list_rag_vat_scopes(db, entity_code)
    ]


def review_formal_vat_completeness_bulk(
    db,
    items: list[dict[str, Any]],
    *,
    reviewed_by: str,
) -> list[dict[str, Any]]:
    """Atomically review a caller-previewed set of legal-entity/month RAG snapshots."""
    results: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda row: (str(row.get("entity_code")), str(row.get("period")))):
        results.append(
            review_formal_vat_completeness(
                db,
                str(item.get("entity_code") or ""),
                str(item.get("period") or ""),
                expected_output_vat_total=_money(item.get("expected_output_vat_total")),
                expected_input_vat_total=_money(item.get("expected_input_vat_total")),
                expected_snapshot_sha256=str(item.get("expected_snapshot_sha256") or "") or None,
                opening_input_credit=(
                    _money(item.get("opening_input_credit"))
                    if item.get("opening_input_credit") is not None
                    else None
                ),
                reviewed_by=reviewed_by,
            )
        )
    return results


__all__ = [
    "FormalVatCompletenessReviewError",
    "get_formal_vat_completeness_review",
    "list_formal_vat_completeness_reviews",
    "review_formal_vat_completeness",
    "review_formal_vat_completeness_bulk",
]
