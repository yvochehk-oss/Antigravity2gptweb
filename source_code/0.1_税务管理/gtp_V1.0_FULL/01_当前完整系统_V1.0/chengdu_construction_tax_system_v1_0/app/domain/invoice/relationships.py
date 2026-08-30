"""Deterministic Task 07b invoice relationship and projection rules."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from app.v3_fact_models import Fact, FactRelationship, InvoiceFact


RELATIONSHIP_TYPES = frozenset({"REVERSAL_OF", "REPLACES", "VOID_RELATION", "CORRECTS"})
FINAL_INVOICE_STATUSES = frozenset({"VALID", "VOIDED", "RED"})


class InvoiceRelationshipError(ValueError):
    """Raised when an invoice relationship would create ambiguous Fact semantics."""


@dataclass(frozen=True)
class InvoiceProjectionValue:
    invoice_status: str
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal


def _money(value: Decimal | None) -> Decimal:
    return Decimal("0.00") if value is None else Decimal(value)


def project_effective_invoice_totals(
    rows: Iterable[InvoiceProjectionValue],
) -> tuple[Decimal, Decimal, Decimal]:
    """Project economic invoice totals without duplicating or zeroing Facts.

    VOIDED invoices contribute zero. RED invoices remain present and contribute
    their stored negative amounts, so a +100 blue and -100 red naturally net to
    zero while both Facts remain auditable.
    """
    net = Decimal("0.00")
    vat = Decimal("0.00")
    gross = Decimal("0.00")
    for row in rows:
        status = row.invoice_status.upper()
        if status not in FINAL_INVOICE_STATUSES:
            raise InvoiceRelationshipError(f"invoice status is not projectable: {status}")
        if status == "VOIDED":
            continue
        if status == "RED" and (
            row.net_amount > 0 or row.vat_amount > 0 or row.gross_amount > 0
        ):
            raise InvoiceRelationshipError("RED invoice amounts must be non-positive")
        net += row.net_amount
        vat += row.vat_amount
        gross += row.gross_amount
    return net, vat, gross


def _invoice(session: Session, fact_id: int) -> InvoiceFact | None:
    return session.get(InvoiceFact, int(fact_id))


def _fact(session: Session, fact_id: int) -> Fact:
    fact = session.get(Fact, int(fact_id))
    if fact is None:
        raise InvoiceRelationshipError(f"Fact {fact_id} does not exist")
    return fact


def _assert_same_invoice_parties(source: InvoiceFact, target: InvoiceFact) -> None:
    if source.seller_party_id != target.seller_party_id or source.buyer_party_id != target.buyer_party_id:
        raise InvoiceRelationshipError("related invoices must have the same seller and buyer")
    if source.currency != target.currency:
        raise InvoiceRelationshipError("related invoices must have the same currency")


def link_facts(
    session: Session,
    *,
    source_fact_id: int,
    target_fact_id: int,
    relationship_type: str,
    reason: str | None = None,
) -> FactRelationship:
    """Create one explicit relationship; never infer the target from fuzzy data."""
    relationship_type = relationship_type.strip().upper()
    if relationship_type not in RELATIONSHIP_TYPES:
        raise InvoiceRelationshipError(f"unsupported relationship type: {relationship_type}")
    if int(source_fact_id) == int(target_fact_id):
        raise InvoiceRelationshipError("a Fact cannot relate to itself")

    _fact(session, source_fact_id)
    _fact(session, target_fact_id)
    source_invoice = _invoice(session, source_fact_id)
    target_invoice = _invoice(session, target_fact_id)

    if relationship_type == "REVERSAL_OF":
        if source_invoice is None or target_invoice is None:
            raise InvoiceRelationshipError("REVERSAL_OF requires two Invoice Facts")
        if source_invoice.invoice_status != "RED":
            raise InvoiceRelationshipError("REVERSAL_OF source must be a RED invoice")
        if target_invoice.invoice_status == "RED":
            raise InvoiceRelationshipError("REVERSAL_OF target cannot itself be RED")
        _assert_same_invoice_parties(source_invoice, target_invoice)
        if any(
            value > 0
            for value in (
                _money(source_invoice.net_amount),
                _money(source_invoice.vat_amount),
                _money(source_invoice.gross_amount),
            )
        ):
            raise InvoiceRelationshipError("RED invoice amounts must be non-positive")

    elif relationship_type in {"REPLACES", "CORRECTS"}:
        if source_invoice is None or target_invoice is None:
            raise InvoiceRelationshipError(f"{relationship_type} requires two Invoice Facts")
        _assert_same_invoice_parties(source_invoice, target_invoice)

    elif relationship_type == "VOID_RELATION":
        if target_invoice is None:
            raise InvoiceRelationshipError("VOID_RELATION target must be an Invoice Fact")
        if target_invoice.invoice_status != "VOIDED":
            raise InvoiceRelationshipError("VOID_RELATION target must have invoice_status=VOIDED")

    relationship = FactRelationship(
        source_fact_id=int(source_fact_id),
        target_fact_id=int(target_fact_id),
        relationship_type=relationship_type,
        reason=reason,
    )
    session.add(relationship)
    session.flush()
    return relationship


def record_void_event(
    session: Session,
    *,
    target_invoice_fact_id: int,
    event_business_identity_key: str,
    reason: str,
) -> FactRelationship:
    """Record a void as an auditable event Fact plus VOID_RELATION.

    The invoice amount is not rewritten to zero. Projection excludes VOIDED
    status, while the event/relationship preserves why the invoice ceased to
    contribute.
    """
    target = _invoice(session, target_invoice_fact_id)
    if target is None:
        raise InvoiceRelationshipError("void target must be an Invoice Fact")
    key = event_business_identity_key.strip()
    if not key:
        raise InvoiceRelationshipError("event_business_identity_key is required")
    if not reason.strip():
        raise InvoiceRelationshipError("void reason is required")

    target.invoice_status = "VOIDED"
    event = Fact(
        fact_type="INVOICE_STATUS_EVENT",
        business_identity_key=key,
        validation_status="VALID",
        is_current=True,
    )
    session.add(event)
    session.flush()
    return link_facts(
        session,
        source_fact_id=event.id,
        target_fact_id=target_invoice_fact_id,
        relationship_type="VOID_RELATION",
        reason=reason,
    )
