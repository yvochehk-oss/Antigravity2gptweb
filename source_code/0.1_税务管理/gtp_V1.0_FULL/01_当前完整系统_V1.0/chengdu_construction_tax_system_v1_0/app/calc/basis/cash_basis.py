"""Task16 CASH-basis adapter.

Canonical CASH analysis requires PaymentFact/TaxPayment. Until V3 PaymentFact is
implemented, callers must fail closed rather than silently falling back to the
legacy cashflows table.
"""
from __future__ import annotations

from .contracts import (
    BasisResult,
    BasisSource,
    BasisStatus,
    CalculationBasis,
    SourceKind,
    SourceRole,
    assert_source_allowed,
)

LEGACY_CASHFLOW_FALLBACK_ALLOWED = False


class CashBasisNotReady(RuntimeError):
    pass


def validate_cash_source(kind: SourceKind, role: SourceRole) -> None:
    assert_source_allowed(CalculationBasis.CASH, BasisSource(kind, role))


def require_canonical_payment_fact(*, payment_fact_available: bool) -> BasisResult:
    if not payment_fact_available:
        raise CashBasisNotReady(
            "CASH basis requires V3 PaymentFact; legacy cashflows are not a canonical fallback"
        )
    validate_cash_source(SourceKind.PAYMENT_FACT, SourceRole.PRIMARY_AMOUNT)
    return BasisResult(
        basis=CalculationBasis.CASH,
        status=BasisStatus.COMPLETE,
        source_count=1,
    )
