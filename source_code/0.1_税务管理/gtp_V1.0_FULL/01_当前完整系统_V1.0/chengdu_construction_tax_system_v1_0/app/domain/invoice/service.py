"""Database adapter for Task 07a invoice validation and state promotion."""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.v3_fact_models import Fact, InvoiceFact, InvoiceLine

from .validation import (
    InvoiceLineInput,
    InvoiceValidationInput,
    InvoiceValidationResult,
    validate_invoice_values,
)


class InvoiceFactStateError(ValueError):
    """Raised when a non-Invoice or non-promotable Fact is validated."""


def validate_invoice_fact(
    session: Session,
    fact_id: int,
    *,
    allowed_tax_rates: Iterable[Decimal],
    promote: bool = False,
) -> InvoiceValidationResult:
    """Validate an Invoice Fact and optionally promote DRAFT/NEEDS_REVIEW to VALID.

    ``allowed_tax_rates`` must come from reviewed/versioned business rules. This
    service deliberately has no numeric fallback.
    """
    fact = session.get(Fact, fact_id)
    invoice = session.get(InvoiceFact, fact_id)
    if fact is None or invoice is None:
        raise InvoiceFactStateError(f"invoice fact {fact_id} not found")
    if fact.fact_type != "INVOICE":
        raise InvoiceFactStateError(f"fact {fact_id} is not INVOICE")
    if fact.validation_status == "SUPERSEDED" or not fact.is_current:
        raise InvoiceFactStateError(f"fact {fact_id} is not current/promotable")

    rows = session.execute(
        select(InvoiceLine)
        .where(InvoiceLine.invoice_fact_id == fact_id)
        .order_by(InvoiceLine.line_no, InvoiceLine.id)
    ).scalars().all()

    payload = InvoiceValidationInput(
        seller_party_id=invoice.seller_party_id,
        buyer_party_id=invoice.buyer_party_id,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        gross_amount=invoice.gross_amount,
        net_amount=invoice.net_amount,
        vat_amount=invoice.vat_amount,
        lines=tuple(
            InvoiceLineInput(
                line_no=row.line_no,
                net_amount=row.net_amount,
                vat_amount=row.vat_amount,
                tax_rate=row.tax_rate,
            )
            for row in rows
        ),
    )
    result = validate_invoice_values(payload, allowed_tax_rates=allowed_tax_rates)

    if promote:
        if fact.validation_status not in {"DRAFT", "NEEDS_REVIEW"}:
            raise InvoiceFactStateError(
                f"fact {fact_id} status {fact.validation_status} cannot promote to VALID"
            )
        fact.validation_status = "VALID" if result.valid else "NEEDS_REVIEW"
        session.flush()
    return result
