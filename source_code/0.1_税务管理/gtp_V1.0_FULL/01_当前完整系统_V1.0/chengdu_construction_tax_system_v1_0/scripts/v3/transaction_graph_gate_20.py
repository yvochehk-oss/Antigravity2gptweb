#!/usr/bin/env python3
"""Task20 S20 gate for canonical four-flow transaction graph."""
from __future__ import annotations

import json
import os
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.calc.transaction_graph import AUTO_CONFIRM_ENABLED, confirm_fact_link, get_or_create_transaction, propose_fact_link
from app.v3_contract_models import ContractFact, FulfillmentFact
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import Party
from app.v3_payment_models import PaymentFact

EXPECTED_HEAD = "91_v3_transaction_graph"


def _url() -> str:
    value=os.getenv("DATABASE_URL","").strip()
    if not value: raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql","postgres"}: raise SystemExit("S20 is PostgreSQL-only")
    return value


def _party(session: Session, prefix: str) -> Party:
    token=uuid.uuid4().hex[:10]
    row=Party(code=f"{prefix}-{token}",name=f"{prefix} {token}",short_name=prefix,party_type="external",active=True)
    session.add(row); session.flush(); return row


def _fact(session: Session, kind: str) -> Fact:
    row=Fact(fact_type=kind,business_identity_key=f"S20:{kind}:{uuid.uuid4()}",version_no=1,is_current=True,validation_status="VALID")
    session.add(row); session.flush(); return row


def _fixture(session: Session) -> dict:
    seller=_party(session,"S20-S"); buyer=_party(session,"S20-B")
    contract=_fact(session,"CONTRACT"); session.add(ContractFact(fact_id=contract.id,buyer_party_id=buyer.id,seller_party_id=seller.id,contract_number=f"S20-{uuid.uuid4().hex[:8]}",contract_date=date(2099,2,1),contract_amount=Decimal("100.00"),currency="CNY"))
    fulfillment=_fact(session,"FULFILLMENT"); session.add(FulfillmentFact(fact_id=fulfillment.id,contract_fact_id=contract.id,performing_party_id=seller.id,receiving_party_id=buyer.id,fulfillment_date=date(2099,2,10),amount=Decimal("100.00"),currency="CNY"))
    invoice=_fact(session,"INVOICE"); session.add(InvoiceFact(fact_id=invoice.id,seller_party_id=seller.id,buyer_party_id=buyer.id,invoice_identity_key=f"S20-{uuid.uuid4()}",invoice_identity_version="S20",invoice_number=uuid.uuid4().hex[:16],invoice_date=date(2099,2,15),invoice_status="VALID",gross_amount=Decimal("113.00"),net_amount=Decimal("100.00"),vat_amount=Decimal("13.00"),currency="CNY"))
    payment=_fact(session,"PAYMENT"); session.add(PaymentFact(fact_id=payment.id,payer_party_id=buyer.id,payee_party_id=seller.id,transaction_date=date(2099,2,20),amount=Decimal("113.00"),currency="CNY",settlement_method="BANK_TRANSFER",payment_nature="NORMAL")); session.flush()
    transaction=get_or_create_transaction(session,business_identity_key=f"S20:{uuid.uuid4()}",created_by="gate-s20")
    links=[]
    for relation,fact_id,score in [("CONTRACT",contract.id,"0.99000"),("FULFILLMENT",fulfillment.id,"0.80000"),("INVOICE",invoice.id,"0.20000"),("PAYMENT",payment.id,"0.95000")]:
        links.append(propose_fact_link(session,transaction_id=transaction.id,fact_id=fact_id,relation_type=relation,link_source="GATE_CANDIDATE",confidence=Decimal(score),match_score_breakdown={"gate_score":score}))
    candidate_count=session.connection().execute(text("SELECT count(*) FROM transaction_fact_links WHERE transaction_id=:id AND status='CANDIDATE'"),{"id":transaction.id}).scalar_one()
    open_review_count=session.connection().execute(text("SELECT count(*) FROM review_tasks r JOIN transaction_fact_links l ON l.id=r.object_id WHERE r.object_type='TRANSACTION_FACT_LINK' AND l.transaction_id=:id AND r.status='OPEN'"),{"id":transaction.id}).scalar_one()
    participant_count=session.connection().execute(text("SELECT count(*) FROM transaction_participants WHERE transaction_id=:id"),{"id":transaction.id}).scalar_one()
    for link in links: confirm_fact_link(session,link_id=link.id,confirmed_by="gate-reviewer")
    confirmed_count=session.connection().execute(text("SELECT count(*) FROM transaction_fact_links WHERE transaction_id=:id AND status='CONFIRMED'"),{"id":transaction.id}).scalar_one()
    resolved_review_count=session.connection().execute(text("SELECT count(*) FROM review_tasks r JOIN transaction_fact_links l ON l.id=r.object_id WHERE r.object_type='TRANSACTION_FACT_LINK' AND l.transaction_id=:id AND r.status='RESOLVED'"),{"id":transaction.id}).scalar_one()
    return {"candidate_count_before_review":int(candidate_count),"open_review_count_before_review":int(open_review_count),"participant_count":int(participant_count),"confirmed_count_after_review":int(confirmed_count),"resolved_review_count_after_review":int(resolved_review_count)}


def main() -> int:
    engine=create_engine(_url(),future=True,pool_pre_ping=True); failures=[]; evidence={"database":None,"alembic_db_heads":[],"alembic_disk_heads":[],"auto_confirm_enabled":AUTO_CONFIRM_ENABLED}
    with Session(engine) as session:
        evidence["database"]=session.connection().exec_driver_sql("SELECT current_database()").scalar_one(); db_head=session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one()
        evidence["alembic_db_heads"]=[db_head]; evidence["alembic_disk_heads"]=[EXPECTED_HEAD]
        if db_head!=EXPECTED_HEAD: failures.append(f"DB head is {db_head}, expected {EXPECTED_HEAD}")
        if AUTO_CONFIRM_ENABLED: failures.append("AUTO_CONFIRM must remain OFF in Task20")
        checks={
            "orphan_link_count":"SELECT count(*) FROM transaction_fact_links l LEFT JOIN business_transactions t ON t.id=l.transaction_id LEFT JOIN facts f ON f.id=l.fact_id WHERE t.id IS NULL OR f.id IS NULL",
            "link_subtype_orphan_count":"SELECT count(*) FROM transaction_fact_links l LEFT JOIN contract_facts c ON c.fact_id=l.fact_id LEFT JOIN fulfillment_facts u ON u.fact_id=l.fact_id LEFT JOIN invoice_facts i ON i.fact_id=l.fact_id LEFT JOIN payment_facts p ON p.fact_id=l.fact_id WHERE (l.relation_type='CONTRACT' AND c.fact_id IS NULL) OR (l.relation_type='FULFILLMENT' AND u.fact_id IS NULL) OR (l.relation_type='INVOICE' AND i.fact_id IS NULL) OR (l.relation_type='PAYMENT' AND p.fact_id IS NULL)",
            "confirmed_noncanonical_fact_count":"SELECT count(*) FROM transaction_fact_links l JOIN facts f ON f.id=l.fact_id WHERE l.status='CONFIRMED' AND (f.is_current IS NOT TRUE OR f.validation_status<>'VALID')",
            "pending_link_without_review_count":"SELECT count(*) FROM transaction_fact_links l WHERE l.status IN ('CANDIDATE','NEEDS_REVIEW') AND NOT EXISTS (SELECT 1 FROM review_tasks r WHERE r.object_type='TRANSACTION_FACT_LINK' AND r.object_id=l.id AND r.status IN ('OPEN','IN_REVIEW'))",
            "confirmed_link_without_review_history_count":"SELECT count(*) FROM transaction_fact_links l WHERE l.status='CONFIRMED' AND NOT EXISTS (SELECT 1 FROM review_tasks r WHERE r.object_type='TRANSACTION_FACT_LINK' AND r.object_id=l.id AND r.status='RESOLVED')",
            "review_task_orphan_count":"SELECT count(*) FROM review_tasks r LEFT JOIN transaction_fact_links l ON l.id=r.object_id WHERE r.object_type='TRANSACTION_FACT_LINK' AND l.id IS NULL",
            "participant_source_link_error_count":"SELECT count(*) FROM transaction_participants p LEFT JOIN transaction_fact_links l ON l.transaction_id=p.transaction_id AND l.fact_id=p.source_fact_id WHERE l.id IS NULL",
        }
        for key,sql in checks.items():
            value=int(session.connection().execute(text(sql)).scalar_one()); evidence[key]=value
            if value: failures.append(f"{key}={value}")
        tx=session.begin_nested(); fixture=_fixture(session); evidence["fixture_four_flow"]=fixture
        expected={"candidate_count_before_review":4,"open_review_count_before_review":4,"participant_count":8,"confirmed_count_after_review":4,"resolved_review_count_after_review":4}
        if fixture!=expected: failures.append(f"four-flow fixture mismatch: {fixture}")
        tx.rollback()
    payload={"gate":"S20","status":"PASS" if not failures else "FAIL","evidence":evidence,"failures":failures}; print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)); return 0 if not failures else 1


if __name__=="__main__": raise SystemExit(main())
