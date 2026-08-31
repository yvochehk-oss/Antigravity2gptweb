"""PostgreSQL integration tests for Task20 transaction graph."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.calc.transaction_graph import AUTO_CONFIRM_ENABLED, TransactionGraphError, confirm_fact_link, get_or_create_transaction, propose_fact_link
from app.v3_contract_models import ContractFact, FulfillmentFact
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import Party
from app.v3_payment_models import PaymentFact
from app.v3_transaction_models import ReviewTask, TransactionFactLink, TransactionParticipant


def _party(session: Session, prefix: str) -> Party:
    token = uuid.uuid4().hex[:10]
    row = Party(code=f"{prefix}-{token}", name=f"{prefix} {token}", short_name=prefix, party_type="external", active=True)
    session.add(row); session.flush(); return row


def _fact(session: Session, fact_type: str) -> Fact:
    row = Fact(fact_type=fact_type, business_identity_key=f"task20:{fact_type}:{uuid.uuid4()}", version_no=1, is_current=True, validation_status="VALID")
    session.add(row); session.flush(); return row


def _four_flow(session: Session):
    seller = _party(session, "T20-S")
    buyer = _party(session, "T20-B")
    contract = _fact(session, "CONTRACT")
    session.add(ContractFact(fact_id=contract.id, buyer_party_id=buyer.id, seller_party_id=seller.id, contract_number=f"C-{uuid.uuid4().hex[:8]}", contract_date=date(2099,1,1), contract_amount=Decimal("100.00"), currency="CNY"))
    fulfillment = _fact(session, "FULFILLMENT")
    session.add(FulfillmentFact(fact_id=fulfillment.id, contract_fact_id=contract.id, performing_party_id=seller.id, receiving_party_id=buyer.id, fulfillment_date=date(2099,1,10), amount=Decimal("100.00"), currency="CNY"))
    invoice = _fact(session, "INVOICE")
    session.add(InvoiceFact(fact_id=invoice.id, seller_party_id=seller.id, buyer_party_id=buyer.id, invoice_identity_key=f"T20-{uuid.uuid4()}", invoice_identity_version="TASK20", invoice_number=uuid.uuid4().hex[:16], invoice_date=date(2099,1,15), invoice_status="VALID", gross_amount=Decimal("113.00"), net_amount=Decimal("100.00"), vat_amount=Decimal("13.00"), currency="CNY"))
    payment = _fact(session, "PAYMENT")
    session.add(PaymentFact(fact_id=payment.id, payer_party_id=buyer.id, payee_party_id=seller.id, transaction_date=date(2099,1,20), amount=Decimal("113.00"), currency="CNY", settlement_method="BANK_TRANSFER", payment_nature="NORMAL"))
    session.flush()
    return seller, buyer, {"CONTRACT":contract.id,"FULFILLMENT":fulfillment.id,"INVOICE":invoice.id,"PAYMENT":payment.id}


def test_task20_schema_exists(seeded_app, postgres_test_database_url):
    engine=create_engine(postgres_test_database_url,future=True); tables=set(inspect(engine).get_table_names())
    assert {"business_transactions","transaction_fact_links","transaction_participants","review_tasks"} <= tables


def test_auto_confirm_is_off():
    assert AUTO_CONFIRM_ENABLED is False


def test_candidate_creates_review_task_and_participants(seeded_app, postgres_test_database_url):
    engine=create_engine(postgres_test_database_url,future=True)
    with Session(engine) as session:
        tx=session.begin(); _,_,facts=_four_flow(session)
        transaction=get_or_create_transaction(session,business_identity_key=f"T20-{uuid.uuid4()}",created_by="pytest")
        link=propose_fact_link(session,transaction_id=transaction.id,fact_id=facts["INVOICE"],relation_type="INVOICE",link_source="HEURISTIC",confidence=Decimal("0.20000"),match_score_breakdown={"amount":0.2})
        assert link.status=="CANDIDATE"
        assert session.scalar(select(ReviewTask).where(ReviewTask.object_id==link.id,ReviewTask.status=="OPEN")) is not None
        assert len(session.scalars(select(TransactionParticipant).where(TransactionParticipant.transaction_id==transaction.id)).all())==2
        tx.rollback()


def test_relation_type_must_match_fact_type(seeded_app, postgres_test_database_url):
    engine=create_engine(postgres_test_database_url,future=True)
    with Session(engine) as session:
        tx=session.begin(); _,_,facts=_four_flow(session)
        transaction=get_or_create_transaction(session,business_identity_key=f"T20-{uuid.uuid4()}",created_by="pytest")
        with pytest.raises(TransactionGraphError,match="requires fact_type"):
            propose_fact_link(session,transaction_id=transaction.id,fact_id=facts["PAYMENT"],relation_type="INVOICE",link_source="MANUAL")
        tx.rollback()


def test_manual_confirmation_resolves_review_task(seeded_app, postgres_test_database_url):
    engine=create_engine(postgres_test_database_url,future=True)
    with Session(engine) as session:
        tx=session.begin(); _,_,facts=_four_flow(session)
        transaction=get_or_create_transaction(session,business_identity_key=f"T20-{uuid.uuid4()}",created_by="pytest")
        link=propose_fact_link(session,transaction_id=transaction.id,fact_id=facts["PAYMENT"],relation_type="PAYMENT",link_source="SOURCE_DOCUMENT",confidence=Decimal("0.99000"))
        assert link.status=="CANDIDATE"
        confirm_fact_link(session,link_id=link.id,confirmed_by="reviewer")
        assert link.status=="CONFIRMED" and link.confirmed_by=="reviewer"
        task=session.scalar(select(ReviewTask).where(ReviewTask.object_id==link.id)); assert task.status=="RESOLVED" and task.resolved_at is not None
        tx.rollback()


def test_four_flow_creates_eight_role_rows(seeded_app, postgres_test_database_url):
    engine=create_engine(postgres_test_database_url,future=True)
    with Session(engine) as session:
        tx=session.begin(); _,_,facts=_four_flow(session)
        transaction=get_or_create_transaction(session,business_identity_key=f"T20-{uuid.uuid4()}",created_by="pytest")
        for relation,fact_id in facts.items():
            propose_fact_link(session,transaction_id=transaction.id,fact_id=fact_id,relation_type=relation,link_source="MANUAL",confidence=Decimal("0.50000"))
        roles={row.participant_role for row in session.scalars(select(TransactionParticipant).where(TransactionParticipant.transaction_id==transaction.id)).all()}
        assert len(roles)==8
        tx.rollback()


def test_database_rejects_direct_confirmed_insert(seeded_app, postgres_test_database_url):
    engine=create_engine(postgres_test_database_url,future=True)
    with Session(engine) as session:
        tx=session.begin(); _,_,facts=_four_flow(session)
        transaction=get_or_create_transaction(session,business_identity_key=f"T20-{uuid.uuid4()}",created_by="pytest")
        session.add(TransactionFactLink(transaction_id=transaction.id,fact_id=facts["CONTRACT"],relation_type="CONTRACT",allocation_method="EXPLICIT",link_source="MANUAL",status="CONFIRMED",confirmed_by="x",confirmed_at=datetime.now(timezone.utc),match_score_breakdown={}))
        with pytest.raises(DBAPIError): session.flush()
        tx.rollback()


def test_invalid_fact_cannot_enter_graph(seeded_app, postgres_test_database_url):
    engine=create_engine(postgres_test_database_url,future=True)
    with Session(engine) as session:
        tx=session.begin(); fact=_fact(session,"PAYMENT"); fact.validation_status="NEEDS_REVIEW"; session.flush()
        transaction=get_or_create_transaction(session,business_identity_key=f"T20-{uuid.uuid4()}",created_by="pytest")
        with pytest.raises(TransactionGraphError,match="current VALID"):
            propose_fact_link(session,transaction_id=transaction.id,fact_id=fact.id,relation_type="PAYMENT",link_source="MANUAL")
        tx.rollback()
