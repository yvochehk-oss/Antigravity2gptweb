#!/usr/bin/env python3
"""Task14 Entity VAT Ledger builder.

Builds deterministic VAT ledger results from explicit VAT-attribution events.
It never treats invoice_date as Output VAT period and never treats legacy
invoice.deductible/period as confirmed Input VAT. A first-period build requires a
reviewed opening-balance seed; later periods inherit the prior official ledger's
closing Input VAT credit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.domain.vat_ledger import calculate_vat_ledger, month_start, previous_month
from app.v3_fact_models import Fact
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_project_tax_models import TaxPrepaymentFact
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import (
    EntityVatLedger,
    EntityVatLedgerComponent,
    OutputVatEvent,
    VatOpeningBalanceSeed,
)
from app.v3_party_models import InternalEntity

RULESET_VERSION = "V3_ENTITY_VAT_LEDGER_V1"
EXPECTED_HEAD = "84_v3_entity_vat_ledgers"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task14 VAT Ledger builder is PostgreSQL-only")
    return value


def _canonical_hash(payload: Any) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _period(value: str | date) -> date:
    if isinstance(value, date):
        return month_start(value)
    return date.fromisoformat(f"{value}-01" if len(value) == 7 else value).replace(day=1)


def _head(session: Session) -> str:
    value = session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one()
    return str(value)


def _entity(session: Session, entity_code: str) -> InternalEntity:
    row = session.scalar(select(InternalEntity).where(InternalEntity.canonical_code == entity_code))
    if row is None:
        raise ValueError(f"unknown internal entity: {entity_code}")
    return row


def _current_prior_ledger(session: Session, reporting_party_id: int, tax_period: date) -> EntityVatLedger | None:
    prior_period = previous_month(tax_period)
    state = session.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == reporting_party_id,
            TaxPeriodState.tax_type == "VAT",
            TaxPeriodState.tax_period == prior_period,
        )
    )
    if state is None or state.current_run_id is None:
        return None
    return session.scalar(select(EntityVatLedger).where(EntityVatLedger.calculation_run_id == state.current_run_id))


def _opening_source(session: Session, reporting_party_id: int, tax_period: date) -> dict[str, Any]:
    prior = _current_prior_ledger(session, reporting_party_id, tax_period)
    seed = session.scalar(
        select(VatOpeningBalanceSeed).where(
            VatOpeningBalanceSeed.reporting_party_id == reporting_party_id,
            VatOpeningBalanceSeed.tax_period == tax_period,
            VatOpeningBalanceSeed.reviewed.is_(True),
        )
    )
    if prior is not None:
        if seed is not None and Decimal(seed.opening_input_credit) != Decimal(prior.closing_input_credit):
            raise ValueError("reviewed opening seed conflicts with prior ledger closing credit")
        return {
            "kind": "PRIOR_LEDGER",
            "id": prior.id,
            "amount": str(prior.closing_input_credit),
            "prior_period": str(prior.tax_period),
        }
    if seed is None:
        raise ValueError("no prior official VAT ledger and no reviewed opening-balance seed")
    return {"kind": "OPENING_SEED", "id": seed.id, "amount": str(seed.opening_input_credit)}


def _source_snapshot(session: Session, reporting_party_id: int, tax_period: date) -> dict[str, Any]:
    unresolved_input = session.scalars(
        select(InputVatClaim.id).where(
            InputVatClaim.reporting_party_id == reporting_party_id,
            InputVatClaim.claim_period == tax_period,
            InputVatClaim.claim_status == "NEEDS_REVIEW",
        )
    ).all()
    unresolved_output = session.scalars(
        select(OutputVatEvent.id).where(
            OutputVatEvent.reporting_party_id == reporting_party_id,
            OutputVatEvent.output_vat_period == tax_period,
            OutputVatEvent.event_status == "NEEDS_REVIEW",
        )
    ).all()
    if unresolved_input or unresolved_output:
        raise ValueError(
            f"unresolved VAT evidence blocks ledger: input_claim_ids={list(unresolved_input)}, "
            f"output_event_ids={list(unresolved_output)}"
        )

    opening = _opening_source(session, reporting_party_id, tax_period)
    output_rows = session.scalars(
        select(OutputVatEvent).where(
            OutputVatEvent.reporting_party_id == reporting_party_id,
            OutputVatEvent.output_vat_period == tax_period,
            OutputVatEvent.event_status == "CONFIRMED",
        ).order_by(OutputVatEvent.id)
    ).all()
    input_rows = session.scalars(
        select(InputVatClaim).where(
            InputVatClaim.reporting_party_id == reporting_party_id,
            InputVatClaim.claim_period == tax_period,
            InputVatClaim.claim_status == "CONFIRMED",
        ).order_by(InputVatClaim.id)
    ).all()
    prepayment_rows = session.execute(
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

    return {
        "reporting_party_id": reporting_party_id,
        "tax_period": str(tax_period),
        "opening": opening,
        "output_events": [{"id": row.id, "amount": str(row.vat_amount)} for row in output_rows],
        "input_claims": [{"id": row.id, "amount": str(row.claim_amount)} for row in input_rows],
        "tax_prepayments": [{"fact_id": row.fact_id, "amount": str(row.tax_amount)} for row, _ in prepayment_rows],
    }


def _result_from_snapshot(snapshot: dict[str, Any]):
    return calculate_vat_ledger(
        opening_input_credit=Decimal(snapshot["opening"]["amount"]),
        output_vat=sum((Decimal(row["amount"]) for row in snapshot["output_events"]), Decimal("0.00")),
        input_vat=sum((Decimal(row["amount"]) for row in snapshot["input_claims"]), Decimal("0.00")),
        tax_prepayment=sum((Decimal(row["amount"]) for row in snapshot["tax_prepayments"]), Decimal("0.00")),
    )


def discover(session: Session) -> list[dict[str, Any]]:
    candidates = session.execute(
        select(InternalEntity.canonical_code, InternalEntity.party_id).order_by(InternalEntity.canonical_code)
    ).all()
    periods_by_party: dict[int, set[date]] = {}
    for party_id, period in session.execute(select(OutputVatEvent.reporting_party_id, OutputVatEvent.output_vat_period)).all():
        periods_by_party.setdefault(int(party_id), set()).add(period)
    for party_id, period in session.execute(select(InputVatClaim.reporting_party_id, InputVatClaim.claim_period)).all():
        periods_by_party.setdefault(int(party_id), set()).add(period)
    for party_id, period in session.execute(select(VatOpeningBalanceSeed.reporting_party_id, VatOpeningBalanceSeed.tax_period).where(VatOpeningBalanceSeed.reviewed.is_(True))).all():
        periods_by_party.setdefault(int(party_id), set()).add(period)

    results: list[dict[str, Any]] = []
    for code, party_id in candidates:
        for period in sorted(periods_by_party.get(int(party_id), set())):
            try:
                snapshot = _source_snapshot(session, int(party_id), period)
                results.append({
                    "entity_code": code,
                    "reporting_party_id": int(party_id),
                    "period": period.strftime("%Y-%m"),
                    "eligible": True,
                    "snapshot_sha256": _canonical_hash(snapshot),
                    "output_event_count": len(snapshot["output_events"]),
                    "input_claim_count": len(snapshot["input_claims"]),
                    "tax_prepayment_count": len(snapshot["tax_prepayments"]),
                    "opening_source": snapshot["opening"],
                    "blocker": None,
                })
            except ValueError as exc:
                results.append({
                    "entity_code": code,
                    "reporting_party_id": int(party_id),
                    "period": period.strftime("%Y-%m"),
                    "eligible": False,
                    "blocker": str(exc),
                })
    return results


def build_one(
    session: Session,
    *,
    entity_code: str,
    period: str | date,
    created_by: str,
    allow_restatement: bool = False,
) -> dict[str, Any]:
    if _head(session) != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    entity = _entity(session, entity_code)
    tax_period = _period(period)
    snapshot = _source_snapshot(session, entity.party_id, tax_period)
    input_hash = _canonical_hash(snapshot)
    result = _result_from_snapshot(snapshot)

    state = session.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == entity.party_id,
            TaxPeriodState.tax_type == "VAT",
            TaxPeriodState.tax_period == tax_period,
        )
    )
    if state is not None and state.state == "CLOSED" and not allow_restatement:
        raise ValueError("target VAT period is CLOSED; explicit --restatement is required")

    if state is not None and state.current_run_id is not None:
        current_run = session.get(CalculationRun, state.current_run_id)
        current_ledger = session.scalar(select(EntityVatLedger).where(EntityVatLedger.calculation_run_id == state.current_run_id))
        if (
            current_run is not None
            and current_run.run_status == "SUCCEEDED"
            and current_run.ruleset_version == RULESET_VERSION
            and current_run.input_snapshot_sha256 == input_hash
            and current_ledger is not None
        ):
            return {
                "status": "NO_CHANGE",
                "ledger_id": current_ledger.id,
                "calculation_run_id": current_run.id,
                "reporting_party_id": entity.party_id,
                "entity_code": entity_code,
                "tax_period": str(tax_period),
                "input_snapshot_sha256": input_hash,
            }

    run_kind = "RESTATEMENT" if state is not None and state.state == "CLOSED" else "STANDARD"
    supersedes_run_id = state.current_run_id if run_kind == "RESTATEMENT" else None
    run = CalculationRun(
        reporting_party_id=entity.party_id,
        tax_type="VAT",
        tax_period=tax_period,
        run_kind=run_kind,
        run_status="DRAFT",
        ruleset_version=RULESET_VERSION,
        input_snapshot_sha256=input_hash,
        supersedes_run_id=supersedes_run_id,
        created_by=created_by,
        note="Task14 Entity VAT Ledger build",
    )
    session.add(run)
    session.flush()

    ledger = EntityVatLedger(
        calculation_run_id=run.id,
        reporting_party_id=entity.party_id,
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
    session.add(ledger)
    session.flush()

    opening = snapshot["opening"]
    session.add(EntityVatLedgerComponent(
        ledger_id=ledger.id,
        component_type="OPENING_INPUT_CREDIT",
        amount=Decimal(opening["amount"]),
        prior_ledger_id=opening["id"] if opening["kind"] == "PRIOR_LEDGER" else None,
        opening_balance_seed_id=opening["id"] if opening["kind"] == "OPENING_SEED" else None,
    ))
    for row in snapshot["output_events"]:
        session.add(EntityVatLedgerComponent(ledger_id=ledger.id, component_type="OUTPUT_VAT", amount=Decimal(row["amount"]), output_vat_event_id=row["id"]))
    for row in snapshot["input_claims"]:
        session.add(EntityVatLedgerComponent(ledger_id=ledger.id, component_type="INPUT_VAT", amount=Decimal(row["amount"]), input_vat_claim_id=row["id"]))
    for row in snapshot["tax_prepayments"]:
        session.add(EntityVatLedgerComponent(ledger_id=ledger.id, component_type="TAX_PREPAYMENT", amount=Decimal(row["amount"]), tax_prepayment_fact_id=row["fact_id"]))
    session.flush()

    result_payload = {
        "reporting_party_id": entity.party_id,
        "tax_period": str(tax_period),
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
    run.completed_at = datetime.now(timezone.utc)
    session.flush()

    if state is None:
        state = TaxPeriodState(
            reporting_party_id=entity.party_id,
            tax_type="VAT",
            tax_period=tax_period,
            state="OPEN",
            current_run_id=run.id,
        )
        session.add(state)
    else:
        state.current_run_id = run.id
    session.flush()

    return {
        "status": "BUILT",
        "ledger_id": ledger.id,
        "calculation_run_id": run.id,
        "run_kind": run_kind,
        "reporting_party_id": entity.party_id,
        "entity_code": entity_code,
        "tax_period": str(tax_period),
        "input_snapshot_sha256": input_hash,
        "result_sha256": run.result_sha256,
        "result": result_payload,
        "source_snapshot": snapshot,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--entity")
    parser.add_argument("--period")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restatement", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--created-by", default="v3-task14")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with Session(engine) as session:
        if args.discover:
            result: Any = {"kind": "V3_TASK14_VAT_LEDGER_DISCOVERY", "database": make_url(database_url).database, "items": discover(session)}
        else:
            if not args.entity or not args.period:
                raise SystemExit("--entity and --period are required unless --discover is used")
            if not args.apply:
                entity = _entity(session, args.entity)
                period = _period(args.period)
                snapshot = _source_snapshot(session, entity.party_id, period)
                result = {
                    "kind": "V3_TASK14_VAT_LEDGER_PLAN",
                    "database": make_url(database_url).database,
                    "entity_code": args.entity,
                    "reporting_party_id": entity.party_id,
                    "tax_period": str(period),
                    "ruleset_version": RULESET_VERSION,
                    "input_snapshot_sha256": _canonical_hash(snapshot),
                    "source_snapshot": snapshot,
                    "calculation": _result_from_snapshot(snapshot).__dict__,
                }
            else:
                current_db = session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
                if args.confirm_database != current_db:
                    raise SystemExit("--confirm-database must exactly match current_database()")
                result = build_one(
                    session,
                    entity_code=args.entity,
                    period=args.period,
                    created_by=args.created_by,
                    allow_restatement=args.restatement,
                )
                session.commit()
                result = {"kind": "V3_TASK14_VAT_LEDGER_RESULT", "database": current_db, **result}

    engine.dispose()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
