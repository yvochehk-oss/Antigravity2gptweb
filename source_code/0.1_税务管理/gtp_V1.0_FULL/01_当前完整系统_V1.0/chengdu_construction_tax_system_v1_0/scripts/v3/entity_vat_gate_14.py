#!/usr/bin/env python3
"""Read-only Gate S14 verifier for Entity VAT Ledger continuity."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.domain.party.resolver import TaxProfileWindow, resolve_reporting_party
from app.domain.vat_ledger import calculate_vat_ledger, previous_month
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import PartyTaxProfile
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_project_tax_models import TaxPrepaymentFact
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import EntityVatLedger, EntityVatLedgerComponent, OutputVatEvent, VatOpeningBalanceSeed
from scripts.v3.entity_vat_ledger import RULESET_VERSION, _canonical_hash, _source_snapshot

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_HEAD = "84_v3_entity_vat_ledgers"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S14 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    config = Config(str(ROOT / "alembic.ini"))
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        config.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(config).get_heads())


def _profiles(session: Session) -> tuple[TaxProfileWindow, ...]:
    rows = session.scalars(select(PartyTaxProfile)).all()
    return tuple(
        TaxProfileWindow(
            party_id=row.party_id,
            tax_type=row.tax_type,
            reporting_party_id=row.reporting_party_id,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            reviewed=row.reviewed,
        )
        for row in rows
    )


def _component_sums(session: Session, ledger_id: int) -> dict[str, Decimal]:
    values = {"OPENING_INPUT_CREDIT": Decimal("0.00"), "OUTPUT_VAT": Decimal("0.00"), "INPUT_VAT": Decimal("0.00"), "TAX_PREPAYMENT": Decimal("0.00")}
    for row in session.scalars(select(EntityVatLedgerComponent).where(EntityVatLedgerComponent.ledger_id == ledger_id)).all():
        values[row.component_type] += Decimal(row.amount)
    return values


def run() -> dict[str, Any]:
    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    failures: list[str] = []
    evidence: dict[str, Any] = {}

    with Session(engine) as session:
        inspector = inspect(session.connection())
        db_heads = {str(row[0]) for row in session.execute(text("SELECT version_num FROM alembic_version_tax")) if row[0]}
        disk_heads = _disk_heads()
        if db_heads != {EXPECTED_HEAD}:
            failures.append(f"formal DB head must be {EXPECTED_HEAD}: {sorted(db_heads)}")
        if disk_heads != {EXPECTED_HEAD}:
            failures.append(f"disk head must be {EXPECTED_HEAD}: {sorted(disk_heads)}")

        tables = set(inspector.get_table_names(schema="public"))
        required = {"output_vat_events", "vat_opening_balance_seeds", "entity_vat_ledgers", "entity_vat_ledger_components"}
        missing = sorted(required - tables)
        if missing:
            failures.append(f"missing Task14 tables: {missing}")

        output_columns = {row["name"] for row in inspector.get_columns("output_vat_events", schema="public")} if "output_vat_events" in tables else set()
        ledger_columns = {row["name"] for row in inspector.get_columns("entity_vat_ledgers", schema="public")} if "entity_vat_ledgers" in tables else set()
        output_prohibited = sorted(output_columns & {"invoice_date", "project_id", "direction", "deductible", "legacy_period"})
        ledger_prohibited = sorted(ledger_columns & {"project_id", "invoice_date", "deductible", "cashflow_id", "recognized_revenue", "cost_amount"})
        if output_prohibited:
            failures.append(f"output_vat_events contains prohibited attribution columns: {output_prohibited}")
        if ledger_prohibited:
            failures.append(f"entity_vat_ledgers contains prohibited mixed-axis columns: {ledger_prohibited}")

        profiles = _profiles(session)
        confirmed_output_semantic_errors: list[int] = []
        for event, invoice, fact in session.execute(
            select(OutputVatEvent, InvoiceFact, Fact)
            .join(InvoiceFact, InvoiceFact.fact_id == OutputVatEvent.invoice_fact_id)
            .join(Fact, Fact.id == InvoiceFact.fact_id)
            .where(OutputVatEvent.event_status == "CONFIRMED")
        ).all():
            try:
                resolved = resolve_reporting_party(
                    invoice.seller_party_id,
                    event.output_vat_period,
                    profiles,
                    tax_type="VAT",
                    require_reviewed=True,
                ) if invoice.seller_party_id is not None else None
            except Exception:
                resolved = None
            if (
                fact.fact_type != "INVOICE"
                or not fact.is_current
                or fact.validation_status != "VALID"
                or invoice.invoice_status == "VOIDED"
                or resolved != event.reporting_party_id
            ):
                confirmed_output_semantic_errors.append(event.id)

        ledger_formula_errors: list[int] = []
        ledger_component_errors: list[int] = []
        opening_continuity_errors: list[int] = []
        run_scope_errors: list[int] = []
        result_hash_errors: list[int] = []
        official_source_coverage_errors: list[int] = []
        official_unresolved_evidence_errors: list[int] = []

        ledgers = session.scalars(select(EntityVatLedger).order_by(EntityVatLedger.id)).all()
        for ledger in ledgers:
            run_row = session.get(CalculationRun, ledger.calculation_run_id)
            if (
                run_row is None
                or run_row.run_status != "SUCCEEDED"
                or run_row.tax_type != "VAT"
                or run_row.reporting_party_id != ledger.reporting_party_id
                or run_row.tax_period != ledger.tax_period
                or run_row.ruleset_version != RULESET_VERSION
            ):
                run_scope_errors.append(ledger.id)
                continue

            sums = _component_sums(session, ledger.id)
            if (
                sums["OPENING_INPUT_CREDIT"] != Decimal(ledger.opening_input_credit)
                or sums["OUTPUT_VAT"] != Decimal(ledger.output_vat)
                or sums["INPUT_VAT"] != Decimal(ledger.input_vat)
                or sums["TAX_PREPAYMENT"] != Decimal(ledger.tax_prepayment)
            ):
                ledger_component_errors.append(ledger.id)

            expected = calculate_vat_ledger(
                opening_input_credit=Decimal(ledger.opening_input_credit),
                output_vat=Decimal(ledger.output_vat),
                input_vat=Decimal(ledger.input_vat),
                tax_prepayment=Decimal(ledger.tax_prepayment),
            )
            if any([
                expected.vat_payable_before_prepayment != Decimal(ledger.vat_payable_before_prepayment),
                expected.closing_input_credit != Decimal(ledger.closing_input_credit),
                expected.vat_payable_after_prepayment != Decimal(ledger.vat_payable_after_prepayment),
                expected.unapplied_tax_prepayment != Decimal(ledger.unapplied_tax_prepayment),
            ]):
                ledger_formula_errors.append(ledger.id)

            result_payload = {
                "reporting_party_id": ledger.reporting_party_id,
                "tax_period": str(ledger.tax_period),
                "opening_input_credit": str(ledger.opening_input_credit),
                "output_vat": str(ledger.output_vat),
                "input_vat": str(ledger.input_vat),
                "tax_prepayment": str(ledger.tax_prepayment),
                "vat_payable_before_prepayment": str(ledger.vat_payable_before_prepayment),
                "closing_input_credit": str(ledger.closing_input_credit),
                "vat_payable_after_prepayment": str(ledger.vat_payable_after_prepayment),
                "unapplied_tax_prepayment": str(ledger.unapplied_tax_prepayment),
            }
            if run_row.result_sha256 != _canonical_hash(result_payload):
                result_hash_errors.append(ledger.id)

            opening_components = session.scalars(
                select(EntityVatLedgerComponent).where(
                    EntityVatLedgerComponent.ledger_id == ledger.id,
                    EntityVatLedgerComponent.component_type == "OPENING_INPUT_CREDIT",
                )
            ).all()
            if len(opening_components) != 1:
                opening_continuity_errors.append(ledger.id)
            else:
                component = opening_components[0]
                if component.prior_ledger_id is not None:
                    prior = session.get(EntityVatLedger, component.prior_ledger_id)
                    if (
                        prior is None
                        or prior.reporting_party_id != ledger.reporting_party_id
                        or prior.tax_period != previous_month(ledger.tax_period)
                        or Decimal(prior.closing_input_credit) != Decimal(ledger.opening_input_credit)
                    ):
                        opening_continuity_errors.append(ledger.id)
                elif component.opening_balance_seed_id is not None:
                    seed = session.get(VatOpeningBalanceSeed, component.opening_balance_seed_id)
                    if (
                        seed is None
                        or not seed.reviewed
                        or seed.reporting_party_id != ledger.reporting_party_id
                        or seed.tax_period != ledger.tax_period
                        or Decimal(seed.opening_input_credit) != Decimal(ledger.opening_input_credit)
                    ):
                        opening_continuity_errors.append(ledger.id)

        official_states = session.scalars(
            select(TaxPeriodState).where(
                TaxPeriodState.tax_type == "VAT",
                TaxPeriodState.current_run_id.is_not(None),
            )
        ).all()
        official_ledger_ids: list[int] = []
        for state in official_states:
            ledger = session.scalar(select(EntityVatLedger).where(EntityVatLedger.calculation_run_id == state.current_run_id))
            if ledger is None:
                official_source_coverage_errors.append(state.id)
                continue
            official_ledger_ids.append(ledger.id)
            if ledger.reporting_party_id != state.reporting_party_id or ledger.tax_period != state.tax_period:
                official_source_coverage_errors.append(ledger.id)
                continue
            try:
                snapshot = _source_snapshot(session, ledger.reporting_party_id, ledger.tax_period)
            except ValueError:
                official_unresolved_evidence_errors.append(ledger.id)
                continue
            current_run = session.get(CalculationRun, state.current_run_id)
            if current_run is None or current_run.input_snapshot_sha256 != _canonical_hash(snapshot):
                official_source_coverage_errors.append(ledger.id)
                continue
            component_ids = session.scalars(select(EntityVatLedgerComponent).where(EntityVatLedgerComponent.ledger_id == ledger.id)).all()
            actual_output = sorted(row.output_vat_event_id for row in component_ids if row.output_vat_event_id is not None)
            actual_input = sorted(row.input_vat_claim_id for row in component_ids if row.input_vat_claim_id is not None)
            actual_prepay = sorted(row.tax_prepayment_fact_id for row in component_ids if row.tax_prepayment_fact_id is not None)
            if actual_output != sorted(row["id"] for row in snapshot["output_events"]):
                official_source_coverage_errors.append(ledger.id)
            if actual_input != sorted(row["id"] for row in snapshot["input_claims"]):
                official_source_coverage_errors.append(ledger.id)
            if actual_prepay != sorted(row["fact_id"] for row in snapshot["tax_prepayments"]):
                official_source_coverage_errors.append(ledger.id)

        if confirmed_output_semantic_errors:
            failures.append(f"confirmed Output VAT events with invalid invoice/reporting semantics: {confirmed_output_semantic_errors}")
        if run_scope_errors:
            failures.append(f"VAT ledgers with invalid CalculationRun scope/status: {run_scope_errors}")
        if ledger_component_errors:
            failures.append(f"VAT ledgers whose components do not sum to stored totals: {ledger_component_errors}")
        if ledger_formula_errors:
            failures.append(f"VAT ledgers failing deterministic formula recomputation: {ledger_formula_errors}")
        if opening_continuity_errors:
            failures.append(f"VAT ledgers with invalid opening-credit continuity/evidence: {opening_continuity_errors}")
        if result_hash_errors:
            failures.append(f"VAT ledgers whose result hash differs from CalculationRun: {result_hash_errors}")
        if official_source_coverage_errors:
            failures.append(f"current official VAT ledgers missing/stale source coverage: {sorted(set(official_source_coverage_errors))}")
        if official_unresolved_evidence_errors:
            failures.append(f"current official VAT ledgers have unresolved VAT evidence: {official_unresolved_evidence_errors}")
        if not official_ledger_ids:
            failures.append("Task14 requires at least one formal legal-entity/month VAT Ledger pilot")

        evidence = {
            "database": make_url(database_url).database,
            "alembic_db_heads": sorted(db_heads),
            "alembic_disk_heads": sorted(disk_heads),
            "output_event_prohibited_columns": output_prohibited,
            "ledger_prohibited_columns": ledger_prohibited,
            "output_vat_event_count": session.query(OutputVatEvent).count() if "output_vat_events" in tables else 0,
            "confirmed_output_vat_event_count": session.query(OutputVatEvent).filter(OutputVatEvent.event_status == "CONFIRMED").count() if "output_vat_events" in tables else 0,
            "entity_vat_ledger_count": len(ledgers),
            "official_vat_ledger_ids": official_ledger_ids,
            "confirmed_output_semantic_error_ids": confirmed_output_semantic_errors,
            "run_scope_error_ledger_ids": run_scope_errors,
            "component_error_ledger_ids": ledger_component_errors,
            "formula_error_ledger_ids": ledger_formula_errors,
            "opening_continuity_error_ledger_ids": opening_continuity_errors,
            "result_hash_error_ledger_ids": result_hash_errors,
            "official_source_coverage_error_ids": sorted(set(official_source_coverage_errors)),
            "official_unresolved_evidence_error_ids": official_unresolved_evidence_errors,
        }

    engine.dispose()
    return {"status": "PASS" if not failures else "FAIL", "failures": failures, "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    result = run()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
