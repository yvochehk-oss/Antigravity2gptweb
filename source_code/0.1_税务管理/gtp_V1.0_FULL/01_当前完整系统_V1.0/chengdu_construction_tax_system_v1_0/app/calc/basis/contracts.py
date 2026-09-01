"""Task16 TAX / ACCRUAL / CASH basis contracts.

These contracts intentionally live in code rather than new business tables.
They define which source kinds may participate in each analytical basis and in
what role. Unknown or prohibited combinations fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CalculationBasis(str, Enum):
    TAX = "TAX"
    ACCRUAL = "ACCRUAL"
    CASH = "CASH"


class BasisStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NOT_READY = "NOT_READY"


class SourceRole(str, Enum):
    PRIMARY_AMOUNT = "PRIMARY_AMOUNT"
    PERIOD_ATTRIBUTION = "PERIOD_ATTRIBUTION"
    SUPPORTING_EVIDENCE = "SUPPORTING_EVIDENCE"
    POLICY = "POLICY"


class SourceKind(str, Enum):
    INVOICE_FACT = "INVOICE_FACT"
    INPUT_VAT_CLAIM = "INPUT_VAT_CLAIM"
    TAX_PREPAYMENT_FACT = "TAX_PREPAYMENT_FACT"
    TAX_PAYMENT = "TAX_PAYMENT"
    TAX_RULE = "TAX_RULE"

    PROGRESS = "PROGRESS"
    SETTLEMENT = "SETTLEMENT"
    FULFILLMENT_FACT = "FULFILLMENT_FACT"
    ACCRUED_COST = "ACCRUED_COST"
    REAL_COST = "REAL_COST"

    PAYMENT_FACT = "PAYMENT_FACT"


@dataclass(frozen=True)
class BasisSource:
    kind: SourceKind
    role: SourceRole


@dataclass(frozen=True)
class BasisResult:
    basis: CalculationBasis
    status: BasisStatus
    source_count: int
    blockers: tuple[str, ...] = ()


class BasisBoundaryViolation(ValueError):
    """Raised when a source/role pair crosses an analytical-basis boundary."""


_ALLOWED: dict[CalculationBasis, frozenset[BasisSource]] = {
    CalculationBasis.TAX: frozenset(
        {
            BasisSource(SourceKind.INVOICE_FACT, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.INPUT_VAT_CLAIM, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.INPUT_VAT_CLAIM, SourceRole.PERIOD_ATTRIBUTION),
            BasisSource(SourceKind.TAX_PREPAYMENT_FACT, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.TAX_PAYMENT, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.TAX_RULE, SourceRole.POLICY),
        }
    ),
    CalculationBasis.ACCRUAL: frozenset(
        {
            BasisSource(SourceKind.PROGRESS, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.SETTLEMENT, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.FULFILLMENT_FACT, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.ACCRUED_COST, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.REAL_COST, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.INVOICE_FACT, SourceRole.SUPPORTING_EVIDENCE),
        }
    ),
    CalculationBasis.CASH: frozenset(
        {
            BasisSource(SourceKind.PAYMENT_FACT, SourceRole.PRIMARY_AMOUNT),
            BasisSource(SourceKind.TAX_PAYMENT, SourceRole.PRIMARY_AMOUNT),
        }
    ),
}


def allowed_sources(basis: CalculationBasis) -> frozenset[BasisSource]:
    return _ALLOWED[basis]


def is_source_allowed(basis: CalculationBasis, source: BasisSource) -> bool:
    return source in _ALLOWED[basis]


def assert_source_allowed(basis: CalculationBasis, source: BasisSource) -> None:
    if not is_source_allowed(basis, source):
        raise BasisBoundaryViolation(
            f"{source.kind.value}/{source.role.value} is prohibited for {basis.value} basis"
        )
