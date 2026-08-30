"""Pure Task 07a invoice validation rules.

No tax rate is hard-coded here. The caller must provide the reviewed/versioned
rate set that applies to the invoice date and business context.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

MONEY_TOLERANCE = Decimal("0.01")


def _d(value: Decimal | int | str | None) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class InvoiceLineInput:
    line_no: int
    net_amount: Decimal | None
    vat_amount: Decimal | None
    tax_rate: Decimal | None


@dataclass(frozen=True)
class InvoiceValidationInput:
    seller_party_id: int | None
    buyer_party_id: int | None
    invoice_number: str
    invoice_date: date | None
    gross_amount: Decimal | None
    net_amount: Decimal | None
    vat_amount: Decimal | None
    lines: tuple[InvoiceLineInput, ...]


@dataclass(frozen=True)
class InvoiceValidationResult:
    valid: bool
    violations: tuple[str, ...]


def validate_invoice_values(
    invoice: InvoiceValidationInput,
    *,
    allowed_tax_rates: Iterable[Decimal],
) -> InvoiceValidationResult:
    """Validate the v1.2 hard-integrity contract before Fact becomes VALID."""
    violations: list[str] = []
    rates = {_d(value) for value in allowed_tax_rates}
    rates.discard(None)

    if invoice.seller_party_id is None:
        violations.append("SELLER_PARTY_REQUIRED")
    if invoice.buyer_party_id is None:
        violations.append("BUYER_PARTY_REQUIRED")
    if (
        invoice.seller_party_id is not None
        and invoice.buyer_party_id is not None
        and invoice.seller_party_id == invoice.buyer_party_id
    ):
        violations.append("SELLER_EQUALS_BUYER")
    if not str(invoice.invoice_number or "").strip():
        violations.append("INVOICE_NUMBER_REQUIRED")
    if invoice.invoice_date is None:
        violations.append("INVOICE_DATE_REQUIRED")

    net = _d(invoice.net_amount)
    vat = _d(invoice.vat_amount)
    gross = _d(invoice.gross_amount)
    if net is None or vat is None or gross is None:
        violations.append("HEADER_AMOUNTS_REQUIRED")
    elif abs((net + vat) - gross) > MONEY_TOLERANCE:
        violations.append("HEADER_AMOUNT_UNBALANCED")

    if not invoice.lines:
        violations.append("INVOICE_LINES_REQUIRED")
    if not rates:
        violations.append("VERSIONED_TAX_RATE_RULES_REQUIRED")

    line_net = Decimal("0")
    line_vat = Decimal("0")
    seen_line_nos: set[int] = set()
    for line in invoice.lines:
        if line.line_no < 1:
            violations.append(f"LINE_{line.line_no}_NUMBER_INVALID")
        if line.line_no in seen_line_nos:
            violations.append(f"LINE_{line.line_no}_DUPLICATE")
        seen_line_nos.add(line.line_no)

        current_net = _d(line.net_amount)
        current_vat = _d(line.vat_amount)
        current_rate = _d(line.tax_rate)
        if current_net is None or current_vat is None:
            violations.append(f"LINE_{line.line_no}_AMOUNTS_REQUIRED")
        else:
            line_net += current_net
            line_vat += current_vat
        if current_rate is None:
            violations.append(f"LINE_{line.line_no}_TAX_RATE_REQUIRED")
        elif rates and current_rate not in rates:
            violations.append(f"LINE_{line.line_no}_TAX_RATE_NOT_ALLOWED")

    if net is not None and abs(line_net - net) > MONEY_TOLERANCE:
        violations.append("LINE_NET_SUM_MISMATCH")
    if vat is not None and abs(line_vat - vat) > MONEY_TOLERANCE:
        violations.append("LINE_VAT_SUM_MISMATCH")
    if gross is not None and abs((line_net + line_vat) - gross) > MONEY_TOLERANCE:
        violations.append("LINE_GROSS_SUM_MISMATCH")

    unique_violations = tuple(dict.fromkeys(violations))
    return InvoiceValidationResult(valid=not unique_violations, violations=unique_violations)
