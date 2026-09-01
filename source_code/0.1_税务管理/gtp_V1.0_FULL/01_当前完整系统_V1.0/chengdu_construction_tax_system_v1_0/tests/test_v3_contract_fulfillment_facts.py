"""Task 11 Contract/Fulfillment Fact schema and domain contracts."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.contract_fulfillment import (
    ContractParties,
    FulfillmentParties,
    derive_internal_trade,
    fulfillment_may_be_independent,
    validate_distinct_contract_parties,
    validate_distinct_fulfillment_parties,
)
from app.v3_contract_models import ContractFact, FulfillmentFact
from app.v3_fact_models import Fact
from app.v3_party_models import InternalEntity, Party

ROOT = Path(__file__).resolve().parents[1]


def test_revision_81_is_additive_and_follows_80():
    migration = (
        ROOT / "alembic" / "versions" / "81_v3_contract_fulfillment_facts.py"
    ).read_text(encoding="utf-8")
    upper = migration.upper()
    assert 'down_revision = "80_v3_input_vat_claims"' in migration
    assert '"contract_facts"' in migration
    assert '"fulfillment_facts"' in migration
    assert "DROP TABLE CONTRACTS" not in upper
    assert "DROP TABLE FULFILLMENT" not in upper
    assert "ALTER TABLE CONTRACTS" not in upper


def test_contract_schema_has_no_project_or_internal_trade_duplicate_truth():
    columns = set(ContractFact.__table__.c.keys())
    assert "project_id" not in columns
    assert "internal_trade" not in columns
    assert {"buyer_party_id", "seller_party_id", "contract_amount"}.issubset(columns)


def test_fulfillment_schema_is_independent_and_has_no_project_axis():
    columns = set(FulfillmentFact.__table__.c.keys())
    assert "project_id" not in columns
    assert "evidence_complete" not in columns
    assert "contract_fact_id" in columns
    assert FulfillmentFact.__table__.c.contract_fact_id.nullable is True
    assert fulfillment_may_be_independent(None) is True


def test_internal_trade_is_derived_only_from_internal_party_membership():
    internal = {1, 2, 3}
    assert derive_internal_trade(ContractParties(1, 2), internal) is True
    assert derive_internal_trade(ContractParties(1, 9), internal) is False
    assert derive_internal_trade(ContractParties(None, 2), internal) is None
    assert derive_internal_trade(ContractParties(1, None), internal) is None


def test_party_validation_rejects_same_party_on_both_sides():
    with pytest.raises(ValueError):
        validate_distinct_contract_parties(ContractParties(7, 7))
    with pytest.raises(ValueError):
        validate_distinct_fulfillment_parties(FulfillmentParties(8, 8))


def _party(session: Session, code: str, party_type: str = "external") -> Party:
    row = Party(
        code=code,
        name=code,
        short_name=code[:20],
        party_type=party_type,
        active=True,
    )
    session.add(row)
    session.flush()
    return row


def test_postgresql_contract_and_independent_fulfillment_are_distinct_facts(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            buyer = _party(session, "T11-BUYER", "internal")
            seller = _party(session, "T11-SELLER", "external")
            performer = _party(session, "T11-PERFORMER", "external")
            receiver = _party(session, "T11-RECEIVER", "internal")
            session.add_all(
                [
                    InternalEntity(
                        party_id=buyer.id,
                        canonical_code="T11B",
                        business_role="A",
                        legal_entity=True,
                        active=True,
                    ),
                    InternalEntity(
                        party_id=receiver.id,
                        canonical_code="T11R",
                        business_role="B",
                        legal_entity=True,
                        active=True,
                    ),
                ]
            )

            contract_fact = Fact(
                fact_type="CONTRACT",
                business_identity_key="CONTRACT|T11|001",
                validation_status="NEEDS_REVIEW",
            )
            fulfillment_fact = Fact(
                fact_type="FULFILLMENT",
                business_identity_key="FULFILLMENT|T11|001",
                validation_status="NEEDS_REVIEW",
            )
            session.add_all([contract_fact, fulfillment_fact])
            session.flush()
            session.add(
                ContractFact(
                    fact_id=contract_fact.id,
                    buyer_party_id=buyer.id,
                    seller_party_id=seller.id,
                    contract_number="T11-C-001",
                    contract_amount=Decimal("1000.00"),
                    currency="CNY",
                )
            )
            session.add(
                FulfillmentFact(
                    fact_id=fulfillment_fact.id,
                    contract_fact_id=None,
                    performing_party_id=performer.id,
                    receiving_party_id=receiver.id,
                    fulfillment_kind="DELIVERY",
                    amount=Decimal("250.00"),
                    currency="CNY",
                )
            )
            session.flush()

            assert contract_fact.id != fulfillment_fact.id
            assert session.get(FulfillmentFact, fulfillment_fact.id).contract_fact_id is None
        finally:
            session.close()
            tx.rollback()


def test_postgresql_same_party_contract_is_rejected(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party = _party(session, "T11-SAME")
            fact = Fact(
                fact_type="CONTRACT",
                business_identity_key="CONTRACT|T11|SAME",
                validation_status="NEEDS_REVIEW",
            )
            session.add(fact)
            session.flush()
            nested = session.begin_nested()
            session.add(
                ContractFact(
                    fact_id=fact.id,
                    buyer_party_id=party.id,
                    seller_party_id=party.id,
                    contract_amount=Decimal("1.00"),
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()
