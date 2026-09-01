"""Deterministic Entity VAT Ledger rules.

The ledger keeps Input VAT credit continuity separate from tax prepayment.
Input VAT attribution is supplied by confirmed InputVatClaim events and Output
VAT attribution by confirmed OutputVatEvent events; invoice dates are not used
as tax periods here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable


ZERO = Decimal("0.00")


def month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def previous_month(value: date) -> date:
    value = month_start(value)
    if value.month == 1:
        return date(value.year - 1, 12, 1)
    return date(value.year, value.month - 1, 1)


def next_month(value: date) -> date:
    value = month_start(value)
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def earliest_changed_period(periods: Iterable[date]) -> date | None:
    normalized = [month_start(period) for period in periods]
    return min(normalized) if normalized else None


def rebuild_periods(start: date, end: date) -> tuple[date, ...]:
    start = month_start(start)
    end = month_start(end)
    if end < start:
        raise ValueError("rebuild end precedes start")
    values: list[date] = []
    current = start
    while current <= end:
        values.append(current)
        current = next_month(current)
    return tuple(values)


@dataclass(frozen=True)
class VatLedgerResult:
    opening_input_credit: Decimal
    output_vat: Decimal
    input_vat: Decimal
    tax_prepayment: Decimal
    vat_payable_before_prepayment: Decimal
    closing_input_credit: Decimal
    vat_payable_after_prepayment: Decimal
    unapplied_tax_prepayment: Decimal


def calculate_vat_ledger(
    *,
    opening_input_credit: Decimal,
    output_vat: Decimal,
    input_vat: Decimal,
    tax_prepayment: Decimal,
) -> VatLedgerResult:
    opening = Decimal(opening_input_credit)
    output = Decimal(output_vat)
    input_amount = Decimal(input_vat)
    prepayment = Decimal(tax_prepayment)
    if opening < ZERO:
        raise ValueError("opening Input VAT credit cannot be negative")

    net_before_prepayment = output - input_amount - opening
    payable_before = max(net_before_prepayment, ZERO)
    closing_credit = max(-net_before_prepayment, ZERO)

    payable_after = max(payable_before - prepayment, ZERO)
    unapplied_prepayment = max(prepayment - payable_before, ZERO)

    return VatLedgerResult(
        opening_input_credit=opening,
        output_vat=output,
        input_vat=input_amount,
        tax_prepayment=prepayment,
        vat_payable_before_prepayment=payable_before,
        closing_input_credit=closing_credit,
        vat_payable_after_prepayment=payable_after,
        unapplied_tax_prepayment=unapplied_prepayment,
    )


def assert_credit_continuity(*, prior_closing_credit: Decimal, current_opening_credit: Decimal) -> None:
    if Decimal(prior_closing_credit) != Decimal(current_opening_credit):
        raise ValueError(
            "VAT continuity mismatch: current opening credit must equal prior closing credit"
        )
