"""Task20 deterministic transaction-graph linking and human review workflow.

The matcher never auto-confirms. Scores are evidence for review, not authority to
create a confirmed business relationship.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.v3_contract_models import ContractFact, FulfillmentFact
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_payment_models import PaymentFact
from app.v3_transaction_models import BusinessTransaction, ReviewTask, TransactionFactLink, TransactionParticipant

AUTO_CONFIRM_ENABLED = False
RELATION_FACT_TYPE = {"CONTRACT":"CONTRACT","FULFILLMENT":"FULFILLMENT","INVOICE":"INVOICE","PAYMENT":"PAYMENT"}


class TransactionGraphError(ValueError):
    pass


def get_or_create_transaction(session: Session, *, business_identity_key: str, created_by: str, note: str | None = None) -> BusinessTransaction:
    key=business_identity_key.strip(); actor=created_by.strip()
    if not key or not actor: raise TransactionGraphError("business_identity_key and created_by are required")
    existing=session.scalar(select(BusinessTransaction).where(BusinessTransaction.business_identity_key==key))
    if existing is not None: return existing
    row=BusinessTransaction(business_identity_key=key,created_by=actor,note=note); session.add(row); session.flush(); return row


def _canonical_fact(session: Session, fact_id: int, relation_type: str) -> Fact:
    relation=relation_type.strip().upper(); expected=RELATION_FACT_TYPE.get(relation)
    if expected is None: raise TransactionGraphError(f"unsupported relation_type: {relation_type}")
    fact=session.get(Fact,fact_id)
    if fact is None: raise TransactionGraphError(f"fact not found: {fact_id}")
    if not fact.is_current or fact.validation_status!="VALID": raise TransactionGraphError("transaction graph only accepts current VALID facts")
    if fact.fact_type!=expected: raise TransactionGraphError(f"relation_type {relation} requires fact_type {expected}, got {fact.fact_type}")
    return fact


def _required_detail(row: Any, *, relation_type: str, fact_id: int) -> Any:
    if row is None: raise TransactionGraphError(f"{relation_type} fact {fact_id} is missing its canonical subtype row")
    return row


def _participants(session: Session, *, fact_id: int, relation_type: str) -> list[tuple[int | None,str]]:
    relation=relation_type.upper()
    if relation=="CONTRACT":
        row=_required_detail(session.get(ContractFact,fact_id),relation_type=relation,fact_id=fact_id)
        return [(row.buyer_party_id,"CONTRACT_BUYER"),(row.seller_party_id,"CONTRACT_SELLER")]
    if relation=="FULFILLMENT":
        row=_required_detail(session.get(FulfillmentFact,fact_id),relation_type=relation,fact_id=fact_id)
        return [(row.performing_party_id,"FULFILLMENT_PROVIDER"),(row.receiving_party_id,"FULFILLMENT_RECEIVER")]
    if relation=="INVOICE":
        row=_required_detail(session.get(InvoiceFact,fact_id),relation_type=relation,fact_id=fact_id)
        return [(row.seller_party_id,"INVOICE_SELLER"),(row.buyer_party_id,"INVOICE_BUYER")]
    row=_required_detail(session.get(PaymentFact,fact_id),relation_type=relation,fact_id=fact_id)
    return [(row.payer_party_id,"PAYMENT_PAYER"),(row.payee_party_id,"PAYMENT_PAYEE")]


def _ensure_participants(session: Session, *, transaction_id: int, fact_id: int, relation_type: str) -> None:
    for party_id,role in _participants(session,fact_id=fact_id,relation_type=relation_type):
        if party_id is None: continue
        existing=session.scalar(select(TransactionParticipant).where(TransactionParticipant.transaction_id==transaction_id,TransactionParticipant.source_fact_id==fact_id,TransactionParticipant.participant_role==role))
        if existing is None: session.add(TransactionParticipant(transaction_id=transaction_id,party_id=int(party_id),source_fact_id=fact_id,participant_role=role))
    session.flush()


def _ensure_review_task(session: Session, *, link_id: int, reason: str, priority: str) -> ReviewTask:
    existing=session.scalar(select(ReviewTask).where(ReviewTask.object_type=="TRANSACTION_FACT_LINK",ReviewTask.object_id==link_id,ReviewTask.status.in_(["OPEN","IN_REVIEW"])))
    if existing is not None: return existing
    task=ReviewTask(object_type="TRANSACTION_FACT_LINK",object_id=link_id,reason=reason,priority=priority,status="OPEN"); session.add(task); session.flush(); return task


def propose_fact_link(session: Session, *, transaction_id: int, fact_id: int, relation_type: str, allocated_amount: Decimal | None = None, allocation_method: str = "EXPLICIT", link_source: str, confidence: Decimal | None = None, match_score_breakdown: dict[str,Any] | None = None, review_reason: str = "Task20 transaction link requires human review", priority: str = "P2") -> TransactionFactLink:
    if AUTO_CONFIRM_ENABLED: raise TransactionGraphError("Task20 requires AUTO_CONFIRM_ENABLED=False")
    if session.get(BusinessTransaction,transaction_id) is None: raise TransactionGraphError(f"transaction not found: {transaction_id}")
    relation=relation_type.strip().upper(); _canonical_fact(session,fact_id,relation)
    if confidence is not None and not (Decimal("0")<=Decimal(confidence)<=Decimal("1")): raise TransactionGraphError("confidence must be between 0 and 1")
    existing=session.scalar(select(TransactionFactLink).where(TransactionFactLink.transaction_id==transaction_id,TransactionFactLink.fact_id==fact_id,TransactionFactLink.relation_type==relation))
    if existing is None:
        existing=TransactionFactLink(transaction_id=transaction_id,fact_id=fact_id,relation_type=relation,allocated_amount=allocated_amount,allocation_method=allocation_method,link_source=link_source,confidence=confidence,match_score_breakdown=match_score_breakdown or {},status="CANDIDATE")
        session.add(existing); session.flush()
    elif existing.status in {"REJECTED","CONFIRMED"}: return existing
    _ensure_participants(session,transaction_id=transaction_id,fact_id=fact_id,relation_type=relation)
    _ensure_review_task(session,link_id=existing.id,reason=review_reason,priority=priority)
    return existing


def mark_link_needs_review(session: Session, *, link_id: int, reason: str) -> TransactionFactLink:
    link=session.get(TransactionFactLink,link_id)
    if link is None: raise TransactionGraphError(f"link not found: {link_id}")
    if link.status!="CANDIDATE": raise TransactionGraphError("only CANDIDATE links may enter NEEDS_REVIEW")
    link.status="NEEDS_REVIEW"; _ensure_review_task(session,link_id=link.id,reason=reason,priority="P2"); session.flush(); return link


def _resolve_tasks(session: Session, *, link_id: int, resolution_note: str) -> None:
    now=datetime.now(timezone.utc)
    tasks=session.scalars(select(ReviewTask).where(ReviewTask.object_type=="TRANSACTION_FACT_LINK",ReviewTask.object_id==link_id,ReviewTask.status.in_(["OPEN","IN_REVIEW"]))).all()
    if not tasks: raise TransactionGraphError("link cannot be reviewed without an open review task")
    for task in tasks: task.status="RESOLVED"; task.resolved_at=now; task.resolution_note=resolution_note


def confirm_fact_link(session: Session, *, link_id: int, confirmed_by: str, resolution_note: str = "Confirmed after human review") -> TransactionFactLink:
    reviewer=confirmed_by.strip()
    if not reviewer: raise TransactionGraphError("confirmed_by is required")
    link=session.get(TransactionFactLink,link_id)
    if link is None: raise TransactionGraphError(f"link not found: {link_id}")
    if link.status not in {"CANDIDATE","NEEDS_REVIEW"}: raise TransactionGraphError("only CANDIDATE/NEEDS_REVIEW links may be confirmed")
    _canonical_fact(session,link.fact_id,link.relation_type); _participants(session,fact_id=link.fact_id,relation_type=link.relation_type)
    _resolve_tasks(session,link_id=link.id,resolution_note=resolution_note)
    link.status="CONFIRMED"; link.confirmed_by=reviewer; link.confirmed_at=datetime.now(timezone.utc); session.flush(); return link


def reject_fact_link(session: Session, *, link_id: int, rejected_by: str, resolution_note: str) -> TransactionFactLink:
    if not rejected_by.strip(): raise TransactionGraphError("rejected_by is required")
    link=session.get(TransactionFactLink,link_id)
    if link is None: raise TransactionGraphError(f"link not found: {link_id}")
    if link.status not in {"CANDIDATE","NEEDS_REVIEW"}: raise TransactionGraphError("only CANDIDATE/NEEDS_REVIEW links may be rejected")
    _resolve_tasks(session,link_id=link.id,resolution_note=f"Rejected by {rejected_by.strip()}: {resolution_note}"); link.status="REJECTED"; session.flush(); return link
