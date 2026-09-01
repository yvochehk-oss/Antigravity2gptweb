#!/usr/bin/env python3
"""Read-only Gate S17 verifier for Project Tax Analysis."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.domain.tax.project_tax_analysis import canonical_hash
from app.v3_party_models import InternalEntity
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_project_analysis_models import FactProjectAllocation, ProjectTaxAnalysis, ProjectTaxAnalysisComponent
from app.v3_project_tax_models import TaxPrepaymentFact
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import OutputVatEvent
from scripts.v3.project_tax_analysis import EXPECTED_HEAD, RULESET_VERSION, _project_results, _source_snapshot

ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S17 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    config = Config(str(ROOT / "alembic.ini"))
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        config.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(config).get_heads())


def run() -> dict[str, Any]:
    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    with Session(engine) as session:
        inspector = inspect(session.connection())
        tables = set(inspector.get_table_names(schema="public"))
        db_heads = {str(row[0]) for row in session.execute(text("SELECT version_num FROM alembic_version_tax")) if row[0]}
        disk_heads = _disk_heads()
        if db_heads != {EXPECTED_HEAD}:
            failures.append(f"formal DB head must be {EXPECTED_HEAD}: {sorted(db_heads)}")
        if disk_heads != {EXPECTED_HEAD}:
            failures.append(f"disk head must be {EXPECTED_HEAD}: {sorted(disk_heads)}")
        required_tables = {"fact_project_allocations", "project_tax_analysis", "project_tax_analysis_components"}
        missing = sorted(required_tables - tables)
        if missing:
            failures.append(f"missing Task17 tables: {missing}")
        entity_vat_columns = {row["name"] for row in inspector.get_columns("entity_vat_ledgers", schema="public")} if "entity_vat_ledgers" in tables else set()
        entity_tax_columns = {row["name"] for row in inspector.get_columns("entity_tax_ledgers", schema="public")} if "entity_tax_ledgers" in tables else set()
        project_columns = {row["name"] for row in inspector.get_columns("project_tax_analysis", schema="public")} if "project_tax_analysis" in tables else set()
        entity_vat_project_columns = sorted(entity_vat_columns & {"project_id"})
        entity_tax_project_columns = sorted(entity_tax_columns & {"project_id"})
        prohibited_project_columns = sorted(project_columns & {"recognized_revenue", "recognized_cost", "cashflow_id", "payment_date", "invoice_date"})
        if entity_vat_project_columns:
            failures.append("Entity VAT Ledger must never contain project_id")
        if entity_tax_project_columns:
            failures.append("Entity Tax Ledger must never contain project_id")
        if prohibited_project_columns:
            failures.append(f"project_tax_analysis contains mixed-basis columns: {prohibited_project_columns}")
        trigger_names = set()
        if not missing:
            trigger_names = {row[0] for row in session.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ('fact_project_allocations'::regclass, 'project_tax_analysis'::regclass)"))}
        required_triggers = {"trg_v3_guard_ai_project_allocation_insert", "trg_v3_guard_project_tax_analysis_scope"}
        missing_triggers = sorted(required_triggers - trigger_names)
        if missing_triggers:
            failures.append(f"missing Task17 guard triggers: {missing_triggers}")

        candidate_consumed: list[int] = []
        cross_project_component_ids: list[int] = []
        period_violation_component_ids: list[int] = []
        component_sum_error_ids: list[int] = []
        result_hash_error_ids: list[int] = []
        run_scope_error_ids: list[int] = []
        current_source_stale_state_ids: list[int] = []
        analyses = session.scalars(select(ProjectTaxAnalysis)).all()
        for analysis in analyses:
            run_row = session.get(CalculationRun, analysis.calculation_run_id)
            if run_row is None or run_row.run_status != "SUCCEEDED" or run_row.tax_type != "PROJECT_TAX" or run_row.ruleset_version != RULESET_VERSION or run_row.reporting_party_id != analysis.reporting_party_id or run_row.tax_period != analysis.tax_period or analysis.basis != "TAX":
                run_scope_error_ids.append(analysis.id)
            rows = session.scalars(select(ProjectTaxAnalysisComponent).where(ProjectTaxAnalysisComponent.analysis_id == analysis.id).order_by(ProjectTaxAnalysisComponent.id)).all()
            output_taxable = Decimal("0.00")
            output_vat = Decimal("0.00")
            input_vat = Decimal("0.00")
            prepayment = Decimal("0.00")
            for component in rows:
                if component.fact_project_allocation_id is not None:
                    allocation = session.get(FactProjectAllocation, component.fact_project_allocation_id)
                    if allocation is None or not allocation.is_current or allocation.status != "CONFIRMED":
                        candidate_consumed.append(component.id)
                    elif allocation.project_id != analysis.project_id:
                        cross_project_component_ids.append(component.id)
                if component.component_type == "OUTPUT_VAT":
                    event = session.get(OutputVatEvent, component.output_vat_event_id)
                    if event is None or event.reporting_party_id != analysis.reporting_party_id or event.output_vat_period != analysis.tax_period:
                        period_violation_component_ids.append(component.id)
                    output_vat += Decimal(component.tax_amount)
                    if component.taxable_amount is not None:
                        output_taxable += Decimal(component.taxable_amount)
                elif component.component_type == "INPUT_VAT":
                    claim = session.get(InputVatClaim, component.input_vat_claim_id)
                    if claim is None or claim.reporting_party_id != analysis.reporting_party_id or claim.claim_period != analysis.tax_period:
                        period_violation_component_ids.append(component.id)
                    input_vat += Decimal(component.tax_amount)
                elif component.component_type == "TAX_PREPAYMENT":
                    prepay = session.get(TaxPrepaymentFact, component.tax_prepayment_fact_id)
                    if prepay is None or prepay.project_id != analysis.project_id or prepay.reporting_party_id != analysis.reporting_party_id or prepay.tax_period != analysis.tax_period or prepay.tax_type != "VAT":
                        period_violation_component_ids.append(component.id)
                    prepayment += Decimal(component.tax_amount)
            before = output_vat - input_vat
            after = before - prepayment
            if any((output_taxable != Decimal(analysis.output_taxable_net), output_vat != Decimal(analysis.output_vat), input_vat != Decimal(analysis.claimed_input_vat), prepayment != Decimal(analysis.tax_prepayment), before != Decimal(analysis.net_vat_before_entity_credit), after != Decimal(analysis.net_vat_after_project_prepayment))):
                component_sum_error_ids.append(analysis.id)
            payload = {"project_id": analysis.project_id, "tax_type": analysis.tax_type, "basis": analysis.basis, "output_taxable_net": str(analysis.output_taxable_net), "output_vat": str(analysis.output_vat), "claimed_input_vat": str(analysis.claimed_input_vat), "tax_prepayment": str(analysis.tax_prepayment), "net_vat_before_entity_credit": str(analysis.net_vat_before_entity_credit), "net_vat_after_project_prepayment": str(analysis.net_vat_after_project_prepayment), "allocation_coverage_status": analysis.allocation_coverage_status}
            if canonical_hash(payload) != analysis.result_sha256:
                result_hash_error_ids.append(analysis.id)

        states = session.scalars(select(TaxPeriodState).where(TaxPeriodState.tax_type == "PROJECT_TAX", TaxPeriodState.current_run_id.is_not(None))).all()
        official_analysis_ids: list[int] = []
        partial_analysis_ids: list[int] = []
        for state in states:
            run_row = session.get(CalculationRun, state.current_run_id)
            entity = session.get(InternalEntity, state.reporting_party_id)
            if run_row is None or entity is None:
                current_source_stale_state_ids.append(state.id)
                continue
            try:
                snapshot = _source_snapshot(session, state.reporting_party_id, state.tax_period)
                expected_projects = _project_results(snapshot)
            except ValueError:
                current_source_stale_state_ids.append(state.id)
                continue
            if canonical_hash(snapshot) != run_row.input_snapshot_sha256:
                current_source_stale_state_ids.append(state.id)
                continue
            aggregate_hash = canonical_hash([{key: value for key, value in row.items() if key != "components"} for row in expected_projects])
            if aggregate_hash != run_row.result_sha256:
                current_source_stale_state_ids.append(state.id)
                continue
            actual = session.scalars(select(ProjectTaxAnalysis).where(ProjectTaxAnalysis.calculation_run_id == state.current_run_id)).all()
            actual_by_project = {row.project_id: row for row in actual}
            official_analysis_ids.extend(row.id for row in actual)
            partial_analysis_ids.extend(row.id for row in actual if row.allocation_coverage_status == "PARTIAL")
            if set(actual_by_project) != {int(row["project_id"]) for row in expected_projects}:
                current_source_stale_state_ids.append(state.id)

        if candidate_consumed:
            failures.append(f"non-confirmed/non-current allocations consumed: {candidate_consumed}")
        if cross_project_component_ids:
            failures.append(f"cross-project component leakage: {cross_project_component_ids}")
        if period_violation_component_ids:
            failures.append(f"Tax Event period/scope violations: {period_violation_component_ids}")
        if component_sum_error_ids:
            failures.append(f"project analysis component sum errors: {component_sum_error_ids}")
        if result_hash_error_ids:
            failures.append(f"project analysis result hash errors: {result_hash_error_ids}")
        if run_scope_error_ids:
            failures.append(f"project analysis CalculationRun scope errors: {run_scope_error_ids}")
        if current_source_stale_state_ids:
            failures.append(f"official PROJECT_TAX state is stale or source coverage changed: {current_source_stale_state_ids}")
        if not official_analysis_ids:
            failures.append("Gate S17 requires at least one official PROJECT_TAX project/month pilot")
        evidence.update({
            "database": make_url(database_url).database,
            "alembic_db_heads": sorted(db_heads),
            "alembic_disk_heads": sorted(disk_heads),
            "entity_vat_ledger_project_columns": entity_vat_project_columns,
            "entity_tax_ledger_project_columns": entity_tax_project_columns,
            "project_analysis_prohibited_columns": prohibited_project_columns,
            "missing_guard_triggers": missing_triggers,
            "allocation_count": session.query(FactProjectAllocation).count(),
            "project_tax_analysis_count": len(analyses),
            "official_project_tax_analysis_ids": sorted(official_analysis_ids),
            "partial_project_tax_analysis_ids": sorted(partial_analysis_ids),
            "nonconfirmed_allocation_component_ids": sorted(set(candidate_consumed)),
            "cross_project_component_ids": sorted(set(cross_project_component_ids)),
            "period_violation_component_ids": sorted(set(period_violation_component_ids)),
            "component_sum_error_ids": sorted(set(component_sum_error_ids)),
            "result_hash_error_ids": sorted(set(result_hash_error_ids)),
            "run_scope_error_ids": sorted(set(run_scope_error_ids)),
            "current_source_stale_state_ids": sorted(set(current_source_stale_state_ids)),
        })
    return {"gate": "S17", "status": "PASS" if not failures else "FAIL", "failures": failures, "evidence": evidence}


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
