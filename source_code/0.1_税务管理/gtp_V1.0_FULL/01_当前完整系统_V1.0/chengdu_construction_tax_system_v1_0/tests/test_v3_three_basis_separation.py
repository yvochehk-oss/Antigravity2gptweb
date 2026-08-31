"""Task16 TAX / ACCRUAL / CASH separation contracts."""
from __future__ import annotations

from datetime import date

import pytest

from app.calc.basis.accrual_basis import validate_accrual_source, validate_invoice_supporting_evidence
from app.calc.basis.cash_basis import (
    LEGACY_CASHFLOW_FALLBACK_ALLOWED,
    CashBasisNotReady,
    require_canonical_payment_fact,
)
from app.calc.basis.contracts import (
    BasisBoundaryViolation,
    BasisSource,
    CalculationBasis,
    SourceKind,
    SourceRole,
    allowed_sources,
    assert_source_allowed,
)
from app.calc.basis.tax_basis import input_vat_period_from_claim, validate_tax_source


def test_tax_basis_accepts_only_tax_semantics():
    validate_tax_source(SourceKind.INVOICE_FACT, SourceRole.PRIMARY_AMOUNT)
    validate_tax_source(SourceKind.INPUT_VAT_CLAIM, SourceRole.PRIMARY_AMOUNT)
    validate_tax_source(SourceKind.INPUT_VAT_CLAIM, SourceRole.PERIOD_ATTRIBUTION)
    validate_tax_source(SourceKind.TAX_PREPAYMENT_FACT, SourceRole.PRIMARY_AMOUNT)
    with pytest.raises(BasisBoundaryViolation):
        validate_tax_source(SourceKind.PAYMENT_FACT, SourceRole.PRIMARY_AMOUNT)
    with pytest.raises(BasisBoundaryViolation):
        validate_tax_source(SourceKind.FULFILLMENT_FACT, SourceRole.PRIMARY_AMOUNT)


def test_payment_is_not_accrual_cost():
    with pytest.raises(BasisBoundaryViolation):
        validate_accrual_source(SourceKind.PAYMENT_FACT, SourceRole.PRIMARY_AMOUNT)


def test_invoice_is_not_revenue_recognition():
    with pytest.raises(BasisBoundaryViolation):
        validate_accrual_source(SourceKind.INVOICE_FACT, SourceRole.PRIMARY_AMOUNT)


def test_invoice_may_be_accrual_supporting_evidence_only():
    validate_invoice_supporting_evidence()


def test_invoice_date_is_not_input_vat_period_source():
    assert input_vat_period_from_claim(date(2025, 9, 1)) == date(2025, 9, 1)
    with pytest.raises(ValueError):
        input_vat_period_from_claim(date(2025, 9, 22))
    with pytest.raises(BasisBoundaryViolation):
        assert_source_allowed(
            CalculationBasis.TAX,
            BasisSource(SourceKind.INVOICE_FACT, SourceRole.PERIOD_ATTRIBUTION),
        )


def test_cash_basis_has_no_legacy_cashflow_fallback():
    assert LEGACY_CASHFLOW_FALLBACK_ALLOWED is False
    with pytest.raises(CashBasisNotReady):
        require_canonical_payment_fact(payment_fact_available=False)


def test_cash_basis_accepts_payment_fact_when_available():
    result = require_canonical_payment_fact(payment_fact_available=True)
    assert result.basis == CalculationBasis.CASH
    assert result.status.value == "COMPLETE"


def test_basis_source_sets_are_disjoint_where_semantics_require_it():
    tax = allowed_sources(CalculationBasis.TAX)
    accrual = allowed_sources(CalculationBasis.ACCRUAL)
    cash = allowed_sources(CalculationBasis.CASH)

    payment_primary = BasisSource(SourceKind.PAYMENT_FACT, SourceRole.PRIMARY_AMOUNT)
    invoice_primary = BasisSource(SourceKind.INVOICE_FACT, SourceRole.PRIMARY_AMOUNT)
    invoice_support = BasisSource(SourceKind.INVOICE_FACT, SourceRole.SUPPORTING_EVIDENCE)

    assert payment_primary in cash
    assert payment_primary not in tax
    assert payment_primary not in accrual
    assert invoice_primary in tax
    assert invoice_primary not in accrual
    assert invoice_support in accrual
    assert invoice_support not in tax
