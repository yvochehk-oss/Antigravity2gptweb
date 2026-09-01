"""Pure Input VAT Claim rules for Task 10.

Invoice date and legacy invoice period are intentionally absent from the ledger
projection. Only confirmed claim events whose ``claim_period`` matches the
requested month contribute to Input VAT.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class InputVatClaimView:
    reporting_party_id: int
    claim_period: date
    claim_amount: Decimal
    claim_status: str


def month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def confirmed_input_vat_total(
    claims: Iterable[InputVatClaimView],
    *,
    reporting_party_id: int,
    claim_period: date,
) -> Decimal:
    """Sum only CONFIRMED events in the requested reporting-party claim month."""
    target = month_start(claim_period)
    total = Decimal("0")
    for claim in claims:
        if (
            int(claim.reporting_party_id) == int(reporting_party_id)
            and month_start(claim.claim_period) == target
            and claim.claim_status == "CONFIRMED"
        ):
            total += Decimal(claim.claim_amount)
    return total.quantize(Decimal("0.01"))
