"""Pure deterministic rules for V3 Project Tax Treatment and prepayment facts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable


class ProjectTaxRuleError(ValueError):
    """Raised when reviewed project-tax rule evidence is ambiguous or invalid."""


@dataclass(frozen=True)
class ProjectTaxTreatmentView:
    id: int
    project_id: int
    tax_type: str
    treatment_code: str
    reporting_party_id: int
    effective_from: date
    effective_to: date | None
    reviewed: bool

    def contains(self, as_of_date: date) -> bool:
        return self.effective_from <= as_of_date and (
            self.effective_to is None or as_of_date <= self.effective_to
        )


@dataclass(frozen=True)
class TaxPrepaymentView:
    project_id: int
    reporting_party_id: int
    tax_type: str
    tax_period: date
    tax_amount: Decimal
    event_type: str
    validation_status: str
    is_current: bool = True


def resolve_project_tax_treatment(
    treatments: Iterable[ProjectTaxTreatmentView],
    *,
    project_id: int,
    tax_type: str,
    as_of_date: date,
    require_reviewed: bool = True,
) -> ProjectTaxTreatmentView | None:
    """Resolve exactly one effective project-tax treatment without guessing."""
    matches = [
        row
        for row in treatments
        if row.project_id == int(project_id)
        and row.tax_type == tax_type
        and row.contains(as_of_date)
        and (row.reviewed or not require_reviewed)
    ]
    if len(matches) > 1:
        raise ProjectTaxRuleError(
            f"multiple active project tax treatments for project={project_id}, "
            f"tax_type={tax_type}, date={as_of_date.isoformat()}"
        )
    return matches[0] if matches else None


def validated_tax_prepayment_total(
    facts: Iterable[TaxPrepaymentView],
    *,
    reporting_party_id: int,
    tax_type: str,
    tax_period: date,
    project_id: int | None = None,
) -> Decimal:
    """Project/reporting-party TAX projection from current VALID prepayment Facts only."""
    month = tax_period.replace(day=1)
    total = Decimal("0.00")
    for row in facts:
        if not row.is_current or row.validation_status != "VALID":
            continue
        if row.reporting_party_id != int(reporting_party_id):
            continue
        if row.tax_type != tax_type or row.tax_period.replace(day=1) != month:
            continue
        if project_id is not None and row.project_id != int(project_id):
            continue
        total += Decimal(row.tax_amount)
    return total
