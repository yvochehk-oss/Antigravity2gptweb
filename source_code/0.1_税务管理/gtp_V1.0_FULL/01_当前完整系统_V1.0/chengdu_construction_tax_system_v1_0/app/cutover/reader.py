"""Task22 Reader / RAG cutover orchestration.

Reader cutover is explicit and reversible. Writer V3_PRIMARY is intentionally
not reversible here: rolling RAG back to legacy never unfreezes legacy writes.
Canonical reads fail closed; there is no exception-driven legacy fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cutover.writer import CutoverError, get_cutover_state, unresolved_production_diff_count
from app.v3_contract_models import ContractFact, FulfillmentFact
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_payment_models import PaymentFact
from app.v3_project_analysis_models import FactProjectAllocation


class ReaderMode(StrEnum):
    SHADOW = "SHADOW"
    PRIMARY = "PRIMARY"


class RagSource(StrEnum):
    LEGACY = "LEGACY"
    CANONICAL_FACTS = "CANONICAL_FACTS"


@dataclass(frozen=True)
class ReaderRoute:
    mode: str
    rag_source: str

    @property
    def is_canonical(self) -> bool:
        return self.mode == ReaderMode.PRIMARY.value and self.rag_source == RagSource.CANONICAL_FACTS.value


def get_reader_route(db: Session) -> ReaderRoute:
    state = get_cutover_state(db)
    pair = (state.new_fact_read_mode, state.rag_source)
    if pair == (ReaderMode.SHADOW.value, RagSource.LEGACY.value):
        return ReaderRoute(*pair)
    if pair == (ReaderMode.PRIMARY.value, RagSource.CANONICAL_FACTS.value):
        return ReaderRoute(*pair)
    raise CutoverError(f"illegal Reader/RAG split state: read_mode={pair[0]}, rag_source={pair[1]}")


def _require_v3_primary_writer(state: Any) -> None:
    if not (
        state.writer_mode == "V3_PRIMARY"
        and state.legacy_write_enabled is False
        and state.new_fact_write_enabled is True
        and state.legacy_frozen is True
    ):
        raise CutoverError("Reader/RAG PRIMARY requires writer V3_PRIMARY with legacy frozen")


def transition_reader_rag_to_primary(db: Session, *, actor: str) -> Any:
    """Explicit forward cutover. Never called automatically."""
    state = get_cutover_state(db, for_update=True)
    if (state.new_fact_read_mode, state.rag_source) != ("SHADOW", "LEGACY"):
        raise CutoverError("Reader/RAG cutover requires SHADOW + LEGACY")
    _require_v3_primary_writer(state)
    blockers = unresolved_production_diff_count(db)
    if blockers:
        raise CutoverError(f"Reader/RAG cutover blocked by {blockers} unresolved production shadow diffs")
    state.new_fact_read_mode = "PRIMARY"
    state.rag_source = "CANONICAL_FACTS"
    state.updated_by = actor
    state.updated_at = datetime.now(timezone.utc)
    db.commit()
    return state


def rollback_reader_rag_to_legacy(db: Session, *, actor: str) -> Any:
    """Read-only rollback; writer remains V3_PRIMARY and legacy stays frozen."""
    state = get_cutover_state(db, for_update=True)
    _require_v3_primary_writer(state)
    if (state.new_fact_read_mode, state.rag_source) != ("PRIMARY", "CANONICAL_FACTS"):
        raise CutoverError("Reader/RAG rollback requires PRIMARY + CANONICAL_FACTS")
    state.new_fact_read_mode = "SHADOW"
    state.rag_source = "LEGACY"
    state.updated_by = actor
    state.updated_at = datetime.now(timezone.utc)
    db.commit()
    return state


def _value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _base_fact(fact: Fact, allocation: FactProjectAllocation) -> dict[str, Any]:
    return {
        "fact_id": fact.id,
        "fact_type": fact.fact_type,
        "business_identity_key": fact.business_identity_key,
        "version_no": fact.version_no,
        "validation_status": fact.validation_status,
        "allocation_id": allocation.id,
        "allocation_method": allocation.allocation_method,
        "allocation_confidence": allocation.confidence,
    }


def _project_fact_rows(db: Session, project_id: int) -> list[tuple[Fact, FactProjectAllocation]]:
    stmt = (
        select(Fact, FactProjectAllocation)
        .join(FactProjectAllocation, FactProjectAllocation.fact_id == Fact.id)
        .where(
            FactProjectAllocation.project_id == project_id,
            FactProjectAllocation.is_current.is_(True),
            FactProjectAllocation.status == "CONFIRMED",
            Fact.is_current.is_(True),
            Fact.validation_status == "VALID",
        )
        .order_by(Fact.id)
    )
    return list(db.execute(stmt).all())


def load_canonical_project_entities(db: Session, project_id: int, *, category: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Load canonical project entities using confirmed FactProjectAllocation only."""
    result: dict[str, list[dict[str, Any]]] = {
        "contracts": [],
        "fulfillment": [],
        "invoices": [],
        "cashflows": [],
        "canonical_facts": [],
    }
    for fact, allocation in _project_fact_rows(db, project_id):
        common = _base_fact(fact, allocation)
        result["canonical_facts"].append(common)

        contract = db.get(ContractFact, fact.id)
        if contract is not None:
            if category is None or contract.contract_category == category:
                result["contracts"].append({**common, "contract_no": contract.contract_number, "contract_date": _value(contract.contract_date), "category": contract.contract_category, "amount": _value(contract.contract_amount), "currency": contract.currency, "buyer_party_id": contract.buyer_party_id, "seller_party_id": contract.seller_party_id})
            continue

        fulfillment = db.get(FulfillmentFact, fact.id)
        if fulfillment is not None:
            if category is None or fulfillment.category == category:
                result["fulfillment"].append({**common, "contract_fact_id": fulfillment.contract_fact_id, "performing_party_id": fulfillment.performing_party_id, "receiving_party_id": fulfillment.receiving_party_id, "fulfillment_date": _value(fulfillment.fulfillment_date), "kind": fulfillment.fulfillment_kind, "category": fulfillment.category, "quantity": _value(fulfillment.quantity), "amount": _value(fulfillment.amount), "currency": fulfillment.currency})
            continue

        invoice = db.get(InvoiceFact, fact.id)
        if invoice is not None:
            result["invoices"].append({**common, "invoice_no": invoice.invoice_number, "invoice_code": invoice.invoice_code, "invoice_date": _value(invoice.invoice_date), "invoice_status": invoice.invoice_status, "invoice_medium": invoice.invoice_medium, "invoice_category": invoice.invoice_category, "gross": _value(invoice.gross_amount), "net": _value(invoice.net_amount), "vat": _value(invoice.vat_amount), "currency": invoice.currency, "seller_party_id": invoice.seller_party_id, "buyer_party_id": invoice.buyer_party_id})
            continue

        payment = db.get(PaymentFact, fact.id)
        if payment is not None:
            result["cashflows"].append({**common, "payer_party_id": payment.payer_party_id, "payee_party_id": payment.payee_party_id, "transaction_date": _value(payment.transaction_date), "amount": _value(payment.amount), "currency": payment.currency, "bank_reference": payment.bank_reference, "settlement_method": payment.settlement_method, "payment_nature": payment.payment_nature})
    return result
