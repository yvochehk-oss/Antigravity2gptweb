"""Deterministic rebuild boundary for the Formal VAT statutory resource.

The write identity is exactly the same as the read identity from
``formal_vat_statutory``: one active legal entity + one statutory month.
A successful rebuild must converge to the same official pointer chain:

    TaxPeriodState.current_run_id -> SUCCEEDED CalculationRun -> EntityVatLedger

The service is PostgreSQL-oriented, fail-closed, and transaction-local.  It does
not commit.  Callers own commit/rollback so the materialization and the pointer
move remain atomic.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any

from sqlalchemy import select, text

from app.domain.vat_ledger import calculate_vat_ledger, month_start, previous_month
from app.v3_fact_models import Fact
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_project_tax_models import TaxPrepaymentFact
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import (
    EntityVatLedger,
    EntityVatLedgerComponent,
    OutputVatEvent,
    VatOpeningBalanceSeed,
)
from app.v3_vat_review_models import VatOutputPeriodAssertion

from .formal_vat_statutory import (
    FormalVatStatutoryResourceIntegrityError,
    FormalVatStatutoryResourceNotFoundError,
    get_formal_vat_statutory_resource,
)
from .legal_entity_fact_periods import LegalEntityNotFoundError
from .legal_entity_scope import _code

RULESET_VERSION = "V3_FORMAL_VAT_STATUTORY_V2"
RESOURCE_TYPE = "FORMAL_VAT_STATUTORY_V1"
PLAN_KIND = "V3_FORMAL_VAT_STATUTORY_REBUILD_PLAN"


class FormalVatRebuildBlockedError(RuntimeError):
    """Raised when evidence/state is insufficient for an official VAT rebuild."""


def _canonical_hash(payload: Any) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _period(value: str | date) -> date:
    if isinstance(value, date):
        return month_start(value)
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


def _period_label(value: date) -> str:
    return value.strftime("%Y-%m")


def _resolve_entity(db, entity_code: str) -> tuple[str, int]:
    wanted = _code(entity_code)
    row = db.execute(
        select(InternalEntity.canonical_code, InternalEntity.party_id)
        .join(Party, Party.id == InternalEntity.party_id)
        .where(
            Party.party_type == "internal",
            Party.active.is_(True),
            InternalEntity.active.is_(True),
            InternalEntity.legal_entity.is_(True),
            InternalEntity.canonical_code == wanted,
        )
    ).one_or_none()
    if row is None:
        raise LegalEntityNotFoundError(wanted)
    return wanted, int(row.party_id)


def _lock_resource(db, reporting_party_id: int, tax_period: date) -> None:
    """Serialize rebuilds for one legal-entity/month inside the transaction."""
    period_key = tax_period.year * 100 + tax_period.month
    db.execute(
        text("SELECT pg_advisory_xact_lock(:party_id, :period_key)"),
        {"party_id": int(reporting_party_id), "period_key": int(period_key)},
    )


def _reviewed_opening_seed(db, reporting_party_id: int, tax_period: date):
    return db.scalar(
        select(VatOpeningBalanceSeed).where(
            VatOpeningBalanceSeed.reporting_party_id == reporting_party_id,
            VatOpeningBalanceSeed.tax_period == tax_period,
            VatOpeningBalanceSeed.reviewed.is_(True),
        )
    )


def _opening_source(
    db,
    *,
    entity_code: str,
    reporting_party_id: int,
    tax_period: date,
) -> dict[str, Any]:
    prior_period = previous_month(tax_period)
    prior_state = db.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == reporting_party_id,
            TaxPeriodState.tax_type == "VAT",
            TaxPeriodState.tax_period == prior_period,
        )
    )
    seed = _reviewed_opening_seed(db, reporting_party_id, tax_period)

    if prior_state is not None:
        try:
            prior = get_formal_vat_statutory_resource(
                db,
                entity_code,
                _period_label(prior_period),
            )
        except FormalVatStatutoryResourceNotFoundError as exc:
            raise FormalVatRebuildBlockedError(
                "prior VAT period exists but has no official statutory resource"
            ) from exc
        except FormalVatStatutoryResourceIntegrityError as exc:
            raise FormalVatRebuildBlockedError(
                "prior VAT statutory resource is inconsistent; current-period rebuild is blocked"
            ) from exc

        prior_amount = Decimal(prior["vat_ledger"]["closing_input_credit"])
        if seed is not None and Decimal(seed.opening_input_credit) != prior_amount:
            raise FormalVatRebuildBlockedError(
                "reviewed opening seed conflicts with prior official VAT closing credit"
            )
        return {
            "kind": "PRIOR_STATUTORY_RESOURCE",
            "ledger_id": int(prior["vat_ledger"]["id"]),
            "calculation_run_id": int(prior["calculation_run"]["id"]),
            "amount": f"{prior_amount:.2f}",
            "prior_period": prior["tax_period"],
        }

    if seed is None:
        raise FormalVatRebuildBlockedError(
            "no prior VAT period and no reviewed opening-balance seed"
        )
    return {
        "kind": "OPENING_SEED",
        "seed_id": int(seed.id),
        "amount": f"{Decimal(seed.opening_input_credit):.2f}",
    }


def _source_snapshot(
    db,
    *,
    entity_code: str,
    reporting_party_id: int,
    tax_period: date,
) -> dict[str, Any]:
    unresolved_input = db.scalars(
        select(InputVatClaim.id).where(
            InputVatClaim.reporting_party_id == reporting_party_id,
            InputVatClaim.claim_period == tax_period,
            InputVatClaim.claim_status == "NEEDS_REVIEW",
        )
    ).all()
    unresolved_output = db.scalars(
        select(OutputVatEvent.id).where(
            OutputVatEvent.reporting_party_id == reporting_party_id,
            OutputVatEvent.output_vat_period == tax_period,
            OutputVatEvent.event_status == "NEEDS_REVIEW",
        )
    ).all()
    if unresolved_input or unresolved_output:
        raise FormalVatRebuildBlockedError(
            "unresolved VAT evidence blocks rebuild: "
            f"input_claim_ids={list(unresolved_input)}, "
            f"output_event_ids={list(unresolved_output)}"
        )

    output_assertion = db.scalar(
        select(VatOutputPeriodAssertion).where(
            VatOutputPeriodAssertion.reporting_party_id == reporting_party_id,
            VatOutputPeriodAssertion.tax_period == tax_period,
            VatOutputPeriodAssertion.reviewed.is_(True),
        )
    )
    if output_assertion is None:
        raise FormalVatRebuildBlockedError(
            "reviewed Output VAT completeness assertion is required"
        )

    output_rows = db.scalars(
        select(OutputVatEvent)
        .where(
            OutputVatEvent.reporting_party_id == reporting_party_id,
            OutputVatEvent.output_vat_period == tax_period,
            OutputVatEvent.event_status == "CONFIRMED",
        )
        .order_by(OutputVatEvent.id)
    ).all()
    output_total = sum(
        (Decimal(row.vat_amount) for row in output_rows),
        Decimal("0.00"),
    )
    asserted_total = Decimal(output_assertion.asserted_output_vat_total)
    if output_total != asserted_total:
        raise FormalVatRebuildBlockedError(
            "confirmed Output VAT events do not match the reviewed completeness assertion"
        )

    input_rows = db.scalars(
        select(InputVatClaim)
        .where(
            InputVatClaim.reporting_party_id == reporting_party_id,
            InputVatClaim.claim_period == tax_period,
            InputVatClaim.claim_status == "CONFIRMED",
        )
        .order_by(InputVatClaim.id)
    ).all()
    prepayment_rows = db.execute(
        select(TaxPrepaymentFact, Fact)
        .join(Fact, Fact.id == TaxPrepaymentFact.fact_id)
        .where(
            TaxPrepaymentFact.reporting_party_id == reporting_party_id,
            TaxPrepaymentFact.tax_type == "VAT",
            TaxPrepaymentFact.tax_period == tax_period,
            Fact.is_current.is_(True),
            Fact.validation_status == "VALID",
        )
        .order_by(TaxPrepaymentFact.fact_id)
    ).all()

    opening = _opening_source(
        db,
        entity_code=entity_code,
        reporting_party_id=reporting_party_id,
        tax_period=tax_period,
    )
    return {
        "reporting_party_id": reporting_party_id,
        "entity_code": entity_code,
        "tax_period": tax_period.isoformat(),
        "opening": opening,
        "output_completeness_assertion": {
            "id": int(output_assertion.id),
            "asserted_total": f"{asserted_total:.2f}",
        },
        "output_events": [
            {"id": int(row.id), "amount": f"{Decimal(row.vat_amount):.2f}"}
            for row in output_rows
        ],
        "input_claims": [
            {"id": int(row.id), "amount": f"{Decimal(row.claim_amount):.2f}"}
            for row in input_rows
        ],
        "tax_prepayments": [
            {"fact_id": int(row.fact_id), "amount": f"{Decimal(row.tax_amount):.2f}"}
            for row, _fact in prepayment_rows
        ],
    }


def _result_from_snapshot(snapshot: dict[str, Any]):
    return calculate_vat_ledger(
        opening_input_credit=Decimal(snapshot["opening"]["amount"]),
        output_vat=sum(
            (Decimal(row["amount"]) for row in snapshot["output_events"]),
            Decimal("0.00"),
        ),
        input_vat=sum(
            (Decimal(row["amount"]) for row in snapshot["input_claims"]),
            Decimal("0.00"),
        ),
        tax_prepayment=sum(
            (Decimal(row["amount"]) for row in snapshot["tax_prepayments"]),
            Decimal("0.00"),
        ),
    )


def _state_summary(db, reporting_party_id: int, tax_period: date) -> dict[str, Any]:
    state = db.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == reporting_party_id,
            TaxPeriodState.tax_type == "VAT",
            TaxPeriodState.tax_period == tax_period,
        )
    )
    return {
        "state": state.state if state is not None else None,
        "current_run_id": state.current_run_id if state is not None else None,
        "closed_run_id": state.closed_run_id if state is not None else None,
        "state_version": state.state_version if state is not None else None,
        "restatement_required": bool(state is not None and state.state == "CLOSED"),
    }


def make_formal_vat_rebuild_plan(
    db,
    *,
    entity_code: str,
    period: str | date,
) -> dict[str, Any]:
    wanted, party_id = _resolve_entity(db, entity_code)
    tax_period = _period(period)
    snapshot = _source_snapshot(
        db,
        entity_code=wanted,
        reporting_party_id=party_id,
        tax_period=tax_period,
    )
    calculation = _result_from_snapshot(snapshot)
    core = {
        "kind": PLAN_KIND,
        "version": 1,
        "resource_type": RESOURCE_TYPE,
        "entity_code": wanted,
        "reporting_party_id": party_id,
        "tax_period": tax_period.isoformat(),
        "period": _period_label(tax_period),
        "ruleset_version": RULESET_VERSION,
        "input_snapshot_sha256": _canonical_hash(snapshot),
        "period_state": _state_summary(db, party_id, tax_period),
        "source_snapshot": snapshot,
        "calculation": calculation.__dict__,
    }
    return {**core, "plan_digest": _canonical_hash(core)}


def rebuild_formal_vat_statutory_resource(
    db,
    *,
    entity_code: str,
    period: str | date,
    created_by: str,
    allow_restatement: bool = False,
    expected_input_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    """Build or deterministically reuse one official VAT statutory resource."""
    wanted, party_id = _resolve_entity(db, entity_code)
    tax_period = _period(period)
    period_label = _period_label(tax_period)
    _lock_resource(db, party_id, tax_period)

    snapshot = _source_snapshot(
        db,
        entity_code=wanted,
        reporting_party_id=party_id,
        tax_period=tax_period,
    )
    input_hash = _canonical_hash(snapshot)
    if (
        expected_input_snapshot_sha256 is not None
        and input_hash != expected_input_snapshot_sha256
    ):
        raise FormalVatRebuildBlockedError(
            "stale VAT rebuild plan: source snapshot changed after review"
        )
    result = _result_from_snapshot(snapshot)

    state = db.scalar(
        select(TaxPeriodState)
        .where(
            TaxPeriodState.reporting_party_id == party_id,
            TaxPeriodState.tax_type == "VAT",
            TaxPeriodState.tax_period == tax_period,
        )
        .with_for_update()
    )
    if state is not None and state.state == "CLOSED" and not allow_restatement:
        raise FormalVatRebuildBlockedError(
            "target VAT period is CLOSED; explicit restatement is required"
        )

    if state is not None and state.current_run_id is not None:
        current = get_formal_vat_statutory_resource(db, wanted, period_label)
        current_run = current["calculation_run"]
        if (
            current_run["ruleset_version"] == RULESET_VERSION
            and current_run["input_snapshot_sha256"] == input_hash
        ):
            return {
                "status": "NO_CHANGE",
                "resource_type": RESOURCE_TYPE,
                "entity_code": wanted,
                "reporting_party_id": party_id,
                "period": period_label,
                "tax_period": tax_period.isoformat(),
                "calculation_run_id": int(current_run["id"]),
                "ledger_id": int(current["vat_ledger"]["id"]),
                "input_snapshot_sha256": input_hash,
                "result_sha256": current_run["result_sha256"],
                "resource": current,
            }

    now = datetime.now(timezone.utc)
    run_kind = "RESTATEMENT" if state is not None and state.state == "CLOSED" else "STANDARD"
    supersedes_run_id = state.current_run_id if run_kind == "RESTATEMENT" else None
    actor = str(created_by or "").strip()
    if not actor:
        raise ValueError("created_by is required")

    run = CalculationRun(
        reporting_party_id=party_id,
        tax_type="VAT",
        tax_period=tax_period,
        run_kind=run_kind,
        run_status="DRAFT",
        ruleset_version=RULESET_VERSION,
        input_snapshot_sha256=input_hash,
        supersedes_run_id=supersedes_run_id,
        created_by=actor,
        note="FVAT-2 deterministic statutory rebuild",
    )
    db.add(run)
    db.flush()

    ledger = EntityVatLedger(
        calculation_run_id=run.id,
        reporting_party_id=party_id,
        tax_period=tax_period,
        opening_input_credit=result.opening_input_credit,
        output_vat=result.output_vat,
        input_vat=result.input_vat,
        tax_prepayment=result.tax_prepayment,
        vat_payable_before_prepayment=result.vat_payable_before_prepayment,
        closing_input_credit=result.closing_input_credit,
        vat_payable_after_prepayment=result.vat_payable_after_prepayment,
        unapplied_tax_prepayment=result.unapplied_tax_prepayment,
    )
    db.add(ledger)
    db.flush()

    opening = snapshot["opening"]
    db.add(
        EntityVatLedgerComponent(
            ledger_id=ledger.id,
            component_type="OPENING_INPUT_CREDIT",
            amount=Decimal(opening["amount"]),
            prior_ledger_id=(
                opening["ledger_id"]
                if opening["kind"] == "PRIOR_STATUTORY_RESOURCE"
                else None
            ),
            opening_balance_seed_id=(
                opening["seed_id"] if opening["kind"] == "OPENING_SEED" else None
            ),
        )
    )
    for row in snapshot["output_events"]:
        db.add(
            EntityVatLedgerComponent(
                ledger_id=ledger.id,
                component_type="OUTPUT_VAT",
                amount=Decimal(row["amount"]),
                output_vat_event_id=row["id"],
            )
        )
    for row in snapshot["input_claims"]:
        db.add(
            EntityVatLedgerComponent(
                ledger_id=ledger.id,
                component_type="INPUT_VAT",
                amount=Decimal(row["amount"]),
                input_vat_claim_id=row["id"],
            )
        )
    for row in snapshot["tax_prepayments"]:
        db.add(
            EntityVatLedgerComponent(
                ledger_id=ledger.id,
                component_type="TAX_PREPAYMENT",
                amount=Decimal(row["amount"]),
                tax_prepayment_fact_id=row["fact_id"],
            )
        )
    db.flush()

    stable_snapshot = _source_snapshot(
        db,
        entity_code=wanted,
        reporting_party_id=party_id,
        tax_period=tax_period,
    )
    if _canonical_hash(stable_snapshot) != input_hash:
        raise FormalVatRebuildBlockedError(
            "VAT source snapshot changed during rebuild; transaction must be retried"
        )

    result_payload = {
        "reporting_party_id": party_id,
        "tax_period": tax_period.isoformat(),
        "opening_input_credit": str(result.opening_input_credit),
        "output_vat": str(result.output_vat),
        "input_vat": str(result.input_vat),
        "tax_prepayment": str(result.tax_prepayment),
        "vat_payable_before_prepayment": str(result.vat_payable_before_prepayment),
        "closing_input_credit": str(result.closing_input_credit),
        "vat_payable_after_prepayment": str(result.vat_payable_after_prepayment),
        "unapplied_tax_prepayment": str(result.unapplied_tax_prepayment),
    }
    run.result_sha256 = _canonical_hash(result_payload)
    run.run_status = "SUCCEEDED"
    run.completed_at = now
    db.flush()

    if state is None:
        state = TaxPeriodState(
            reporting_party_id=party_id,
            tax_type="VAT",
            tax_period=tax_period,
            state="OPEN",
            current_run_id=run.id,
            state_version=1,
        )
        db.add(state)
    elif state.state == "CLOSED":
        state.current_run_id = run.id
        state.closed_run_id = run.id
        state.closed_by = actor
        state.closed_at = now
        state.state_version = int(state.state_version) + 1
        state.updated_at = now
    else:
        state.current_run_id = run.id
        state.state_version = int(state.state_version) + 1
        state.updated_at = now
    db.flush()

    official = get_formal_vat_statutory_resource(db, wanted, period_label)
    if (
        int(official["calculation_run"]["id"]) != int(run.id)
        or int(official["vat_ledger"]["id"]) != int(ledger.id)
        or official["calculation_run"]["input_snapshot_sha256"] != input_hash
        or official["calculation_run"]["result_sha256"] != run.result_sha256
    ):
        raise FormalVatStatutoryResourceIntegrityError(
            "rebuild did not converge to the official VAT statutory resource"
        )

    return {
        "status": "BUILT",
        "resource_type": RESOURCE_TYPE,
        "entity_code": wanted,
        "reporting_party_id": party_id,
        "period": period_label,
        "tax_period": tax_period.isoformat(),
        "run_kind": run_kind,
        "calculation_run_id": int(run.id),
        "ledger_id": int(ledger.id),
        "input_snapshot_sha256": input_hash,
        "result_sha256": run.result_sha256,
        "resource": official,
    }


__all__ = [
    "FormalVatRebuildBlockedError",
    "PLAN_KIND",
    "RESOURCE_TYPE",
    "RULESET_VERSION",
    "make_formal_vat_rebuild_plan",
    "rebuild_formal_vat_statutory_resource",
]
