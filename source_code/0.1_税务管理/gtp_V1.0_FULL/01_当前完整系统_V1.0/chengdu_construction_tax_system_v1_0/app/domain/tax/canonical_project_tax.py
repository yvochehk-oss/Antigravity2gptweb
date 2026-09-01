"""Task31 read-only adapter for official Task17 Project Tax Analysis.

No tax formula is reimplemented here.  Persisted Task17 components are verified
with V3_PROJECT_TAX_ANALYSIS_V1 helpers before being exposed to consumers.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.tax.project_tax_analysis import (
    PROJECT_TAX_TYPE,
    RULESET_VERSION,
    canonical_hash,
    summarize_project_components,
)
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_project_analysis_models import (
    FactProjectAllocation,
    ProjectTaxAnalysis,
    ProjectTaxAnalysisComponent,
)


TASK31_CANONICAL_TAX_READ_V1 = "TASK31_CANONICAL_TAX_READ_V1"


class CanonicalProjectTaxReadError(RuntimeError):
    pass


def _period(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return date(value.year, value.month, 1)
    text = str(value)
    parsed = date.fromisoformat(f"{text}-01" if len(text) == 7 else text)
    return date(parsed.year, parsed.month, 1)


def _money(value: Any) -> str:
    return str(Decimal(value).quantize(Decimal("0.01")))


class CanonicalProjectTax:
    def __init__(self, session: Session) -> None:
        self.session = session

    def read(
        self,
        project_id: int,
        *,
        reporting_party_id: int | None = None,
        tax_period: str | date | None = None,
    ) -> dict[str, Any]:
        period = _period(tax_period)
        stmt = select(TaxPeriodState).where(
            TaxPeriodState.tax_type == PROJECT_TAX_TYPE,
            TaxPeriodState.current_run_id.is_not(None),
        )
        if reporting_party_id is not None:
            stmt = stmt.where(TaxPeriodState.reporting_party_id == reporting_party_id)
        if period is not None:
            stmt = stmt.where(TaxPeriodState.tax_period == period)
        states = list(self.session.scalars(stmt.order_by(TaxPeriodState.tax_period, TaxPeriodState.id)).all())

        output: list[dict[str, Any]] = []
        for state in states:
            analysis = self.session.scalar(
                select(ProjectTaxAnalysis).where(
                    ProjectTaxAnalysis.calculation_run_id == state.current_run_id,
                    ProjectTaxAnalysis.project_id == project_id,
                    ProjectTaxAnalysis.reporting_party_id == state.reporting_party_id,
                    ProjectTaxAnalysis.tax_period == state.tax_period,
                    ProjectTaxAnalysis.tax_type == "VAT",
                    ProjectTaxAnalysis.basis == "TAX",
                )
            )
            if analysis is None:
                continue
            run = self.session.get(CalculationRun, state.current_run_id)
            if run is None:
                raise CanonicalProjectTaxReadError(f"PROJECT_TAX state {state.id} points to a missing CalculationRun")
            if run.run_status != "SUCCEEDED" or run.tax_type != PROJECT_TAX_TYPE or run.ruleset_version != RULESET_VERSION:
                raise CanonicalProjectTaxReadError(f"PROJECT_TAX current run {run.id} is not an official {RULESET_VERSION} success")
            if run.reporting_party_id != state.reporting_party_id or run.tax_period != state.tax_period:
                raise CanonicalProjectTaxReadError(f"PROJECT_TAX state/run scope mismatch for run {run.id}")
            output.append(self._verified_analysis(state=state, run=run, analysis=analysis))

        return {
            "adapter_version": TASK31_CANONICAL_TAX_READ_V1,
            "task17_ruleset_version": RULESET_VERSION,
            "project_id": int(project_id),
            "availability": "AVAILABLE" if output else "NO_OFFICIAL_PROJECT_TAX_ANALYSIS",
            "analyses": output,
        }

    def _verified_analysis(
        self,
        *,
        state: TaxPeriodState,
        run: CalculationRun,
        analysis: ProjectTaxAnalysis,
    ) -> dict[str, Any]:
        components = list(
            self.session.scalars(
                select(ProjectTaxAnalysisComponent)
                .where(ProjectTaxAnalysisComponent.analysis_id == analysis.id)
                .order_by(ProjectTaxAnalysisComponent.id)
            ).all()
        )
        component_payloads: list[dict[str, Any]] = []
        for component in components:
            if component.fact_project_allocation_id is not None:
                allocation = self.session.get(FactProjectAllocation, component.fact_project_allocation_id)
                if allocation is None or not allocation.is_current or allocation.status != "CONFIRMED":
                    raise CanonicalProjectTaxReadError(
                        f"analysis {analysis.id} consumes non-current/non-confirmed allocation {component.fact_project_allocation_id}"
                    )
                if allocation.project_id != analysis.project_id:
                    raise CanonicalProjectTaxReadError(
                        f"analysis {analysis.id} component {component.id} leaks from another project"
                    )
            component_payloads.append(
                {
                    "component_type": component.component_type,
                    "taxable_amount": component.taxable_amount,
                    "tax_amount": component.tax_amount,
                    "fact_project_allocation_id": component.fact_project_allocation_id,
                    "output_vat_event_id": component.output_vat_event_id,
                    "input_vat_claim_id": component.input_vat_claim_id,
                    "tax_prepayment_fact_id": component.tax_prepayment_fact_id,
                }
            )

        totals = summarize_project_components(component_payloads)
        expected = {
            "output_taxable_net": Decimal(analysis.output_taxable_net),
            "output_vat": Decimal(analysis.output_vat),
            "claimed_input_vat": Decimal(analysis.claimed_input_vat),
            "tax_prepayment": Decimal(analysis.tax_prepayment),
            "net_vat_before_entity_credit": Decimal(analysis.net_vat_before_entity_credit),
            "net_vat_after_project_prepayment": Decimal(analysis.net_vat_after_project_prepayment),
        }
        if any(Decimal(totals[key]) != value for key, value in expected.items()):
            raise CanonicalProjectTaxReadError(f"analysis {analysis.id} no longer matches Task17 component totals")

        result_payload = {
            "project_id": analysis.project_id,
            "tax_type": analysis.tax_type,
            "basis": analysis.basis,
            "output_taxable_net": str(analysis.output_taxable_net),
            "output_vat": str(analysis.output_vat),
            "claimed_input_vat": str(analysis.claimed_input_vat),
            "tax_prepayment": str(analysis.tax_prepayment),
            "net_vat_before_entity_credit": str(analysis.net_vat_before_entity_credit),
            "net_vat_after_project_prepayment": str(analysis.net_vat_after_project_prepayment),
            "allocation_coverage_status": analysis.allocation_coverage_status,
        }
        if canonical_hash(result_payload) != analysis.result_sha256:
            raise CanonicalProjectTaxReadError(f"analysis {analysis.id} result hash mismatch")

        return {
            "analysis_id": int(analysis.id),
            "calculation_run_id": int(run.id),
            "reporting_party_id": int(analysis.reporting_party_id),
            "tax_period": str(analysis.tax_period),
            "period_state": str(state.state),
            "tax_type": analysis.tax_type,
            "basis": analysis.basis,
            "ruleset_version": run.ruleset_version,
            "input_snapshot_sha256": analysis.input_snapshot_sha256,
            "result_sha256": analysis.result_sha256,
            "result_hash_verified": True,
            "allocation_coverage_status": analysis.allocation_coverage_status,
            "output_taxable_net": _money(analysis.output_taxable_net),
            "output_vat": _money(analysis.output_vat),
            "claimed_input_vat": _money(analysis.claimed_input_vat),
            "tax_prepayment": _money(analysis.tax_prepayment),
            "net_vat_before_entity_credit": _money(analysis.net_vat_before_entity_credit),
            "net_vat_after_project_prepayment": _money(analysis.net_vat_after_project_prepayment),
            "component_count": len(components),
        }
