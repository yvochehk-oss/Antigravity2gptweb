"""Task16 ACCRUAL-basis adapter.

Invoices may support accrual evidence, but an invoice amount is not itself
revenue recognition or cost recognition. Payments are never accrual cost.
"""
from __future__ import annotations

from dataclasses import dataclass

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
class AccrualBasisCapability:
    available_primary_sources: tuple[SourceKind, ...]
    blockers: tuple[str, ...] = ()

    def as_result(self) -> BasisResult:
        status = BasisStatus.COMPLETE if self.available_primary_sources and not self.blockers else BasisStatus.PARTIAL
        return BasisResult(
            basis=CalculationBasis.ACCRUAL,
            status=status,
            source_count=len(self.available_primary_sources),
            blockers=self.blockers,
        )


def validate_accrual_source(kind: SourceKind, role: SourceRole) -> None:
    assert_source_allowed(CalculationBasis.ACCRUAL, BasisSource(kind, role))


def validate_invoice_supporting_evidence() -> None:
    validate_accrual_source(SourceKind.INVOICE_FACT, SourceRole.SUPPORTING_EVIDENCE)
