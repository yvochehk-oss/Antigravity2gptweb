"""Task16 TAX-basis adapter.

The TAX basis may use invoice facts, VAT claims, tax prepayments, tax payments
and tax rules. Input VAT period attribution is sourced from claim_period, never
from invoice_date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .contracts import (
    BasisResult,
    BasisSource,
    BasisStatus,
    CalculationBasis,
    SourceKind,
    SourceRole,
    assert_source_allowed,
)


@dataclass(frozen=True)
class TaxBasisSnapshot:
    reporting_party_id: int
    period: date
    source_count: int
    blockers: tuple[str, ...] = ()

    def as_result(self) -> BasisResult:
        return BasisResult(
            basis=CalculationBasis.TAX,
            status=BasisStatus.COMPLETE if not self.blockers else BasisStatus.PARTIAL,
            source_count=self.source_count,
            blockers=self.blockers,
        )


def validate_tax_source(kind: SourceKind, role: SourceRole) -> None:
    assert_source_allowed(CalculationBasis.TAX, BasisSource(kind, role))


def input_vat_period_from_claim(claim_period: date) -> date:
    """Return canonical Input VAT month from the claim event itself."""
    if claim_period.day != 1:
        raise ValueError("Input VAT claim_period must be the first day of its month")
    validate_tax_source(SourceKind.INPUT_VAT_CLAIM, SourceRole.PERIOD_ATTRIBUTION)
    return claim_period
