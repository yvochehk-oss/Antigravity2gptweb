#!/usr/bin/env python3
"""Task17 deterministic Project Tax Analysis PLAN/APPLY builder."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.domain.tax.project_tax_analysis import (
    PROJECT_TAX_TYPE,
    RULESET_VERSION,
    ProjectAllocationError,
    allocate_vat_event,
    allocation_coverage,
    canonical_hash,
    summarize_project_components,
)
from app.models import Project
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_project_analysis_models import (
    FactProjectAllocation,
    ProjectTaxAnalysis,
    ProjectTaxAnalysisComponent,
)
from app.v3_project_tax_models import TaxPrepaymentFact
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import OutputVatEvent

EXPECTED_HEAD = "88_v3_project_tax_analysis"
PLAN_KIND = "V3_TASK17_PROJECT_TAX_ANALYSIS_PLAN"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task17 Project Tax Analysis is PostgreSQL-only")
    return value


def _period(value: str | date) -> date:
    if isinstance(value, date):
        return date(value.year, value.month, 1)
    text_value = str(value)
    parsed = date.fromisoformat(f"{text_value}-01" if len(text_value) == 7 else text_value)
    return date(parsed.year, parsed.month, 1)


def _head(session: Session) -> str:
    return str(
        session.connection()
        .exec_driver_sql("SELECT version_num FROM alembic_version_tax")
        .scalar_one()
    )


def _entity(session: Session, entity_code: str) -> InternalEntity:
    row = session.scalar(
        select(InternalEntity).where(InternalEntity.canonical_code == entity_code)
    )
    if row is None:
        raise ValueError(f"unknown internal entity: {entity_code}")
    if not row.legal_entity:
        raise ValueError("Project Tax Analysis requires a legal reporting entity")
    return row


def _allocation_dict(row: FactProjectAllocation) -> dict[str, Any]:
    return {
        "id": row.id,
        "fact_id": row.fact_id,
        "project_id": row.project_id,
        "allocation_version": row.allocation_version,
        "allocated_net": str(row.allocated_net),
        "allocated_vat": str(row.allocated_vat),
        "allocated_gross": str(row.allocated_gross),
        "allocation_method": row.allocation_method,
        "confidence": row.confidence,
        "status": row.status,
        "is_current": row.is_current,
        "proposal_source": row.proposal_source,
        "source_document_id": row.source_document_id,
        "rule_version": row.rule_version,
    }


def _confirmed_allocations(
    session: Session, fact_ids: set[int]
) -> dict[int, list[FactProjectAllocation]]:
    if not fact_ids:
        return {}
    rows = session.scalars(
        select(FactProjectAllocation)
        .where(
            FactProjectAllocation.fact_id.in_(fact_ids),
            FactProjectAllocation.is_current.is_(True),
            FactProjectAllocation.status == "CONFIRMED",
        )
        .order_by(
            FactProjectAllocation.fact_id,
            FactProjectAllocation.project_id,
            FactProjectAllocation.id,
        )
    ).all()
    grouped: dict[int, list[FactProjectAllocation]] = {}
    for row in rows:
        grouped.setdefault(int(row.fact_id), []).append(row)
    return grouped


def _source_snapshot(
    session: Session, reporting_party_id: int, tax_period: date
) -> dict[str, Any]:
    unresolved_output = session.scalars(
        select(OutputVatEvent.id).where(
            OutputVatEvent.reporting_party_id == reporting_party_id,
            OutputVatEvent.output_vat_period == tax_period,
            OutputVatEvent.event_status == "NEEDS_REVIEW",
        )
    ).all()
    unresolved_input = session.scalars(
        select(InputVatClaim.id).where(
            InputVatClaim.reporting_party_id == reporting_party_id,
            InputVatClaim.claim_period == tax_period,
            InputVatClaim.claim_status == "NEEDS_REVIEW",
        )
    ).all()
    if unresolved_output or unresolved_input:
        raise ValueError(
            "unresolved TAX evidence blocks project analysis: "
            f"output={list(unresolved_output)} input={list(unresolved_input)}"
        )

    output_rows = session.execute(
        select(OutputVatEvent, InvoiceFact, Fact)
        .join(InvoiceFact, InvoiceFact.fact_id == OutputVatEvent.invoice_fact_id)
        .join(Fact, Fact.id == InvoiceFact.fact_id)
        .where(
            OutputVatEvent.reporting_party_id == reporting_party_id,
            OutputVatEvent.output_vat_period == tax_period,
            OutputVatEvent.event_status == "CONFIRMED",
            Fact.is_current.is_(True),
            Fact.validation_status == "VALID",
            InvoiceFact.invoice_status != "VOIDED",
        )
        .order_by(OutputVatEvent.id)
    ).all()
    input_rows = session.execute(
        select(InputVatClaim, InvoiceFact, Fact)
        .join(InvoiceFact, InvoiceFact.fact_id == InputVatClaim.invoice_fact_id)
        .join(Fact, Fact.id == InvoiceFact.fact_id)
        .where(
            InputVatClaim.reporting_party_id == reporting_party_id,
            InputVatClaim.claim_period == tax_period,
            InputVatClaim.claim_status == "CONFIRMED",
            Fact.is_current.is_(True),
            Fact.validation_status == "VALID",
            InvoiceFact.invoice_status != "VOIDED",
        )
        .order_by(InputVatClaim.id)
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

    fact_ids = {
        int(invoice.fact_id) for _, invoice, _ in output_rows
    } | {int(invoice.fact_id) for _, invoice, _ in input_rows}
    allocations = _confirmed_allocations(session, fact_ids)
    coverage_by_fact: dict[int, dict[str, Any]] = {}
    blockers: list[str] = []
    invoices: dict[int, InvoiceFact] = {}
    for _, invoice, _ in [*output_rows, *input_rows]:
        invoices[int(invoice.fact_id)] = invoice

    for fact_id, invoice in sorted(invoices.items()):
        rows = allocations.get(fact_id, [])
        try:
            coverage = allocation_coverage(
                source_net=invoice.net_amount or Decimal("0.00"),
                source_vat=invoice.vat_amount or Decimal("0.00"),
                source_gross=invoice.gross_amount or Decimal("0.00"),
                allocations=rows,
            )
        except ProjectAllocationError as exc:
            blockers.append(f"fact {fact_id}: {exc}")
            continue
        coverage_by_fact[fact_id] = coverage.as_dict()
        if coverage.status == "OVER":
            blockers.append(f"fact {fact_id}: OVER_ALLOCATED")
        elif coverage.status == "NONE":
            blockers.append(f"fact {fact_id}: no CONFIRMED project allocation")

    return {
        "reporting_party_id": reporting_party_id,
        "tax_period": str(tax_period),
        "output_events": [
            {
                "id": event.id,
                "invoice_fact_id": invoice.fact_id,
                "event_type": event.event_type,
                "vat_amount": str(event.vat_amount),
                "invoice_net": str(invoice.net_amount or Decimal("0.00")),
                "invoice_vat": str(invoice.vat_amount or Decimal("0.00")),
                "invoice_gross": str(invoice.gross_amount or Decimal("0.00")),
                "allocations": [
                    _allocation_dict(row)
                    for row in allocations.get(int(invoice.fact_id), [])
                ],
                "coverage": coverage_by_fact.get(int(invoice.fact_id)),
            }
            for event, invoice, _ in output_rows
        ],
        "input_claims": [
            {
                "id": claim.id,
                "invoice_fact_id": invoice.fact_id,
                "event_type": claim.event_type,
                "claim_amount": str(claim.claim_amount),
                "invoice_net": str(invoice.net_amount or Decimal("0.00")),
                "invoice_vat": str(invoice.vat_amount or Decimal("0.00")),
                "invoice_gross": str(invoice.gross_amount or Decimal("0.00")),
                "allocations": [
                    _allocation_dict(row)
                    for row in allocations.get(int(invoice.fact_id), [])
                ],
                "coverage": coverage_by_fact.get(int(invoice.fact_id)),
            }
            for claim, invoice, _ in input_rows
        ],
        "tax_prepayments": [
            {
                "fact_id": prepayment.fact_id,
                "project_id": prepayment.project_id,
                "tax_amount": str(prepayment.tax_amount),
                "taxable_base": (
                    str(prepayment.taxable_base)
                    if prepayment.taxable_base is not None
                    else None
                ),
                "event_type": prepayment.event_type,
            }
            for prepayment, _ in prepayment_rows
        ],
        "blockers": sorted(set(blockers)),
    }


def _project_results(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    if snapshot["blockers"]:
        raise ValueError(
            "Task17 source snapshot has blocking allocation errors: "
            + "; ".join(snapshot["blockers"])
        )

    components: dict[int, list[dict[str, Any]]] = {}
    coverage: dict[int, str] = {}

    for event in snapshot["output_events"]:
        shares = allocate_vat_event(
            event_amount=event["vat_amount"],
            source_vat=event["invoice_vat"],
            source_net=event["invoice_net"],
            allocations=event["allocations"],
        )
        source_coverage = (event["coverage"] or {}).get("status", "NONE")
        for share in shares:
            components.setdefault(share.project_id, []).append(
                {
                    "component_type": "OUTPUT_VAT",
                    "taxable_amount": (
                        str(share.taxable_amount)
                        if share.taxable_amount is not None
                        else None
                    ),
                    "tax_amount": str(share.tax_amount),
                    "fact_project_allocation_id": share.allocation_id,
                    "output_vat_event_id": event["id"],
                    "input_vat_claim_id": None,
                    "tax_prepayment_fact_id": None,
                }
            )
            coverage[share.project_id] = (
                "PARTIAL"
                if source_coverage == "PARTIAL"
                else coverage.get(share.project_id, "FULL")
            )

    for claim in snapshot["input_claims"]:
        shares = allocate_vat_event(
            event_amount=claim["claim_amount"],
            source_vat=claim["invoice_vat"],
            allocations=claim["allocations"],
        )
        source_coverage = (claim["coverage"] or {}).get("status", "NONE")
        for share in shares:
            components.setdefault(share.project_id, []).append(
                {
                    "component_type": "INPUT_VAT",
                    "taxable_amount": None,
                    "tax_amount": str(share.tax_amount),
                    "fact_project_allocation_id": share.allocation_id,
                    "output_vat_event_id": None,
                    "input_vat_claim_id": claim["id"],
                    "tax_prepayment_fact_id": None,
                }
            )
            coverage[share.project_id] = (
                "PARTIAL"
                if source_coverage == "PARTIAL"
                else coverage.get(share.project_id, "FULL")
            )

    for row in snapshot["tax_prepayments"]:
        project_id = int(row["project_id"])
        components.setdefault(project_id, []).append(
            {
                "component_type": "TAX_PREPAYMENT",
                "taxable_amount": row["taxable_base"],
                "tax_amount": row["tax_amount"],
                "fact_project_allocation_id": None,
                "output_vat_event_id": None,
                "input_vat_claim_id": None,
                "tax_prepayment_fact_id": row["fact_id"],
            }
        )
        coverage.setdefault(project_id, "FULL")

    results: list[dict[str, Any]] = []
    for project_id in sorted(components):
        project_components = sorted(
            components[project_id],
            key=lambda row: (
                row["component_type"],
                row["output_vat_event_id"] or 0,
                row["input_vat_claim_id"] or 0,
                row["tax_prepayment_fact_id"] or 0,
                row["fact_project_allocation_id"] or 0,
            ),
        )
        totals = summarize_project_components(project_components)
        result_payload = {
            "project_id": project_id,
            "tax_type": "VAT",
            "basis": "TAX",
            **{key: str(value) for key, value in totals.items()},
            "allocation_coverage_status": coverage.get(project_id, "NONE"),
        }
        results.append(
            {
                **result_payload,
                "result_sha256": canonical_hash(result_payload),
                "components": project_components,
            }
        )
    return results


def _state_summary(
    session: Session, reporting_party_id: int, tax_period: date
) -> dict[str, Any]:
    state = session.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == reporting_party_id,
            TaxPeriodState.tax_type == PROJECT_TAX_TYPE,
            TaxPeriodState.tax_period == tax_period,
        )
    )
    return {
        "state": state.state if state else None,
        "current_run_id": state.current_run_id if state else None,
        "state_version": state.state_version if state else None,
        "restatement_required": bool(state and state.state == "CLOSED"),
    }


def make_plan(
    session: Session, *, entity_code: str, period: str | date
) -> dict[str, Any]:
    if _head(session) != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    entity = _entity(session, entity_code)
    tax_period = _period(period)
    snapshot = _source_snapshot(session, entity.party_id, tax_period)
    projects = _project_results(snapshot)
    if not projects:
        raise ValueError("no project TAX components exist for this reporting party/period")
    core = {
        "kind": PLAN_KIND,
        "version": 1,
        "entity_code": entity_code,
        "reporting_party_id": entity.party_id,
        "tax_period": str(tax_period),
        "tax_type": "VAT",
        "basis": "TAX",
        "ruleset_version": RULESET_VERSION,
        "input_snapshot_sha256": canonical_hash(snapshot),
        "period_state": _state_summary(session, entity.party_id, tax_period),
        "source_snapshot": snapshot,
        "projects": projects,
    }
    return {**core, "plan_digest": canonical_hash(core)}


def _current_results_match_plan(
    current_run: CalculationRun | None,
    current_rows: list[ProjectTaxAnalysis],
    plan: dict[str, Any],
) -> bool:
    if (
        current_run is None
        or current_run.run_status != "SUCCEEDED"
        or current_run.ruleset_version != RULESET_VERSION
        or current_run.input_snapshot_sha256 != plan["input_snapshot_sha256"]
    ):
        return False
    expected = {
        int(row["project_id"]): row["result_sha256"] for row in plan["projects"]
    }
    actual = {row.project_id: row.result_sha256 for row in current_rows}
    return actual == expected


def build_one(
    session: Session,
    *,
    entity_code: str,
    period: str | date,
    created_by: str,
    allow_restatement: bool = False,
    expected_input_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    if _head(session) != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    entity = _entity(session, entity_code)
    tax_period = _period(period)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
        {"scope": f"PROJECT_TAX:{entity.party_id}:{tax_period.isoformat()}"},
    )
    plan = make_plan(session, entity_code=entity_code, period=tax_period)
    input_hash = plan["input_snapshot_sha256"]
    if (
        expected_input_snapshot_sha256 is not None
        and expected_input_snapshot_sha256 != input_hash
    ):
        raise ValueError("stale Task17 plan: source snapshot changed after PLAN review")

    state = session.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == entity.party_id,
            TaxPeriodState.tax_type == PROJECT_TAX_TYPE,
            TaxPeriodState.tax_period == tax_period,
        )
    )
    if state is not None and state.state == "CLOSED" and not allow_restatement:
        raise ValueError(
            "target PROJECT_TAX period is CLOSED; explicit restatement is required"
        )

    if state is not None and state.current_run_id is not None:
        current_run = session.get(CalculationRun, state.current_run_id)
        current_rows = session.scalars(
            select(ProjectTaxAnalysis).where(
                ProjectTaxAnalysis.calculation_run_id == state.current_run_id
            )
        ).all()
        if _current_results_match_plan(current_run, current_rows, plan):
            return {
                "status": "NO_CHANGE",
                "calculation_run_id": current_run.id,
                "analysis_ids": [row.id for row in current_rows],
                "reporting_party_id": entity.party_id,
                "tax_period": str(tax_period),
                "input_snapshot_sha256": input_hash,
            }

    run_kind = (
        "RESTATEMENT"
        if state is not None and state.state == "CLOSED"
        else "STANDARD"
    )
    supersedes_run_id = state.current_run_id if run_kind == "RESTATEMENT" else None
    aggregate_payload = [
        {key: value for key, value in row.items() if key != "components"}
        for row in plan["projects"]
    ]
    aggregate_hash = canonical_hash(aggregate_payload)
    now = datetime.now(timezone.utc)
    run = CalculationRun(
        reporting_party_id=entity.party_id,
        tax_type=PROJECT_TAX_TYPE,
        tax_period=tax_period,
        run_kind=run_kind,
        run_status="SUCCEEDED",
        ruleset_version=RULESET_VERSION,
        input_snapshot_sha256=input_hash,
        result_sha256=aggregate_hash,
        supersedes_run_id=supersedes_run_id,
        created_by=created_by,
        note="Task17 Project Tax Analysis build",
        completed_at=now,
    )
    session.add(run)
    session.flush()

    if state is None:
        state = TaxPeriodState(
            reporting_party_id=entity.party_id,
            tax_type=PROJECT_TAX_TYPE,
            tax_period=tax_period,
            state="OPEN",
            current_run_id=run.id,
        )
        session.add(state)
    else:
        state.current_run_id = run.id
        state.state_version += 1
        state.updated_at = now
    session.flush()

    analysis_ids: list[int] = []
    for result in plan["projects"]:
        project = session.get(Project, int(result["project_id"]))
        if project is None:
            raise ValueError(f"project {result['project_id']} no longer exists")
        row = ProjectTaxAnalysis(
            calculation_run_id=run.id,
            project_id=int(result["project_id"]),
            reporting_party_id=entity.party_id,
            tax_period=tax_period,
            tax_type="VAT",
            basis="TAX",
            output_taxable_net=Decimal(result["output_taxable_net"]),
            output_vat=Decimal(result["output_vat"]),
            claimed_input_vat=Decimal(result["claimed_input_vat"]),
            tax_prepayment=Decimal(result["tax_prepayment"]),
            net_vat_before_entity_credit=Decimal(
                result["net_vat_before_entity_credit"]
            ),
            net_vat_after_project_prepayment=Decimal(
                result["net_vat_after_project_prepayment"]
            ),
            allocation_coverage_status=result["allocation_coverage_status"],
            input_snapshot_sha256=input_hash,
            result_sha256=result["result_sha256"],
        )
        session.add(row)
        session.flush()
        analysis_ids.append(row.id)
        for component in result["components"]:
            session.add(
                ProjectTaxAnalysisComponent(
                    analysis_id=row.id,
                    component_type=component["component_type"],
                    taxable_amount=(
                        Decimal(component["taxable_amount"])
                        if component["taxable_amount"] is not None
                        else None
                    ),
                    tax_amount=Decimal(component["tax_amount"]),
                    fact_project_allocation_id=component[
                        "fact_project_allocation_id"
                    ],
                    output_vat_event_id=component["output_vat_event_id"],
                    input_vat_claim_id=component["input_vat_claim_id"],
                    tax_prepayment_fact_id=component["tax_prepayment_fact_id"],
                )
            )
    session.flush()
    return {
        "status": "BUILT",
        "calculation_run_id": run.id,
        "analysis_ids": analysis_ids,
        "project_count": len(analysis_ids),
        "reporting_party_id": entity.party_id,
        "entity_code": entity_code,
        "tax_period": str(tax_period),
        "basis": "TAX",
        "input_snapshot_sha256": input_hash,
        "result_sha256": aggregate_hash,
    }


def _write_json(path: str | None, payload: Any) -> None:
    rendered = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True, default=str
    )
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--entity", required=True)
    plan_parser.add_argument("--period", required=True)
    plan_parser.add_argument("--json")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--entity", required=True)
    apply_parser.add_argument("--period", required=True)
    apply_parser.add_argument("--created-by", required=True)
    apply_parser.add_argument("--expected-input-sha256", required=True)
    apply_parser.add_argument("--confirm-database", required=True)
    apply_parser.add_argument("--restatement", action="store_true")
    apply_parser.add_argument("--json")
    args = parser.parse_args()
    database_url = _database_url()
    if (
        args.command == "apply"
        and make_url(database_url).database != args.confirm_database
    ):
        raise SystemExit("--confirm-database does not match DATABASE_URL database")
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with Session(engine) as session:
        if args.command == "plan":
            payload = make_plan(
                session, entity_code=args.entity, period=args.period
            )
        else:
            payload = build_one(
                session,
                entity_code=args.entity,
                period=args.period,
                created_by=args.created_by,
                allow_restatement=args.restatement,
                expected_input_snapshot_sha256=args.expected_input_sha256,
            )
            session.commit()
    _write_json(args.json, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
