"""PostgreSQL regression tests for Task18 canonical group penetration."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.calc.penetration.group import (
    ACCRUAL_RECURSIVE_SQL,
    GroupCycleDetected,
    GroupPenetrationError,
    accrual_snapshot,
    require_cash_penetration,
    summarize_accrual,
    summarize_tax,
    tax_snapshot,
)
from app.v3_contract_models import FulfillmentFact
from app.v3_fact_models import Fact
from app.v3_party_models import InternalEntity, Party


def _parties(session: Session):
    for code in ["A08", "B01", "C01"]:
        entity = session.scalar(select(InternalEntity).where(InternalEntity.canonical_code == code))
        if entity is None:
            party = Party(code=code, name=f"{code} Company", short_name=code, party_type="internal", active=True)
            session.add(party)
            session.flush()
            entity = InternalEntity(party_id=party.id, canonical_code=code, business_role="company", legal_entity=True, active=True)
            session.add(entity)
            session.flush()
    for ext_code in ["EXT-T18-1", "EXT-T18-2"]:
        ext = session.scalar(select(Party).where(Party.code == ext_code))
        if ext is None:
            ext = Party(code=ext_code, name=f"External {ext_code}", short_name=ext_code, party_type="external", active=True)
            session.add(ext)
            session.flush()
    internal = {
        row.canonical_code: int(row.party_id)
        for row in session.scalars(
            select(InternalEntity).where(InternalEntity.canonical_code.in_(["A08", "B01", "C01"]))
        ).all()
    }
    assert set(internal) == {"A08", "B01", "C01"}
    external = session.scalars(select(Party).where(Party.party_type == "external").order_by(Party.id).limit(2)).all()
    assert len(external) >= 2
    return internal, int(external[0].id), int(external[1].id)


def _fulfillment(session: Session, *, performer: int, receiver: int, when: date, amount: str) -> int:
    fact = Fact(
        fact_type="FULFILLMENT",
        business_identity_key=f"task18-test:{uuid.uuid4()}",
        version_no=1,
        is_current=True,
        validation_status="VALID",
    )
    session.add(fact)
    session.flush()
    session.add(FulfillmentFact(
        fact_id=fact.id,
        performing_party_id=performer,
        receiving_party_id=receiver,
        fulfillment_date=when,
        fulfillment_kind="TASK18_TEST",
        amount=Decimal(amount),
        currency="CNY",
    ))
    session.flush()
    return int(fact.id)


def test_task18_schema_exists(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names(schema="public"))
    assert "group_penetration_results" in tables
    assert "group_penetration_components" in tables


def test_recursive_cte_is_postgresql_recursive():
    assert "WITH RECURSIVE" in str(ACCRUAL_RECURSIVE_SQL).upper()


def test_external_b_a_owner_chain_eliminates_internal_transfer(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        transaction = session.begin()
        internal, supplier, owner = _parties(session)
        when = date(2099, 1, 15)
        _fulfillment(session, performer=supplier, receiver=internal["B01"], when=when, amount="255.00")
        _fulfillment(session, performer=internal["B01"], receiver=internal["A08"], when=when, amount="300.00")
        _fulfillment(session, performer=internal["A08"], receiver=owner, when=when, amount="350.00")
        result = summarize_accrual(accrual_snapshot(session, period=date(2099, 1, 1)))
        assert result["external_revenue"] == Decimal("350.00")
        assert result["external_leaf_cost"] == Decimal("255.00")
        assert result["internal_eliminated"] == Decimal("300.00")
        assert result["group_gross_margin"] == Decimal("95.00")
        transaction.rollback()


def test_external_c_b_a_owner_chain_keeps_only_external_leaf_cost(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        transaction = session.begin()
        internal, supplier, owner = _parties(session)
        when = date(2099, 2, 15)
        _fulfillment(session, performer=supplier, receiver=internal["C01"], when=when, amount="200.00")
        _fulfillment(session, performer=internal["C01"], receiver=internal["B01"], when=when, amount="230.00")
        _fulfillment(session, performer=internal["B01"], receiver=internal["A08"], when=when, amount="260.00")
        _fulfillment(session, performer=internal["A08"], receiver=owner, when=when, amount="330.00")
        result = summarize_accrual(accrual_snapshot(session, period=date(2099, 2, 1)))
        assert result["external_revenue"] == Decimal("330.00")
        assert result["external_leaf_cost"] == Decimal("200.00")
        assert result["internal_eliminated"] == Decimal("490.00")
        assert result["group_gross_margin"] == Decimal("130.00")
        transaction.rollback()


def test_cycle_is_detected_and_blocks_accrual_result(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        transaction = session.begin()
        internal, _, owner = _parties(session)
        when = date(2099, 3, 15)
        _fulfillment(session, performer=internal["A08"], receiver=owner, when=when, amount="400.00")
        _fulfillment(session, performer=internal["B01"], receiver=internal["A08"], when=when, amount="300.00")
        _fulfillment(session, performer=internal["C01"], receiver=internal["B01"], when=when, amount="280.00")
        _fulfillment(session, performer=internal["A08"], receiver=internal["C01"], when=when, amount="260.00")
        with pytest.raises(GroupCycleDetected, match="CYCLE_DETECTED"):
            accrual_snapshot(session, period=date(2099, 3, 1))
        transaction.rollback()


def test_tax_basis_preserves_all_legal_entity_vat_without_elimination():
    result = summarize_tax({"ledgers": [
        {"output_vat": "13.00", "input_vat": "7.00", "tax_prepayment": "1.00", "vat_payable_after_prepayment": "5.00"},
        {"output_vat": "6.00", "input_vat": "6.00", "tax_prepayment": "0.00", "vat_payable_after_prepayment": "0.00"},
    ]})
    assert result["tax_output_vat"] == Decimal("19.00")
    assert result["tax_input_vat"] == Decimal("13.00")
    assert result["tax_prepayment"] == Decimal("1.00")
    assert result["tax_payable_after_prepayment"] == Decimal("5.00")


def test_tax_penetration_fails_closed_without_official_vat_ledger(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        with pytest.raises(GroupPenetrationError, match="no current official Entity VAT Ledgers"):
            tax_snapshot(session, period=date(2099, 12, 1))


def test_cash_penetration_contract_is_available_after_task19():
    result = require_cash_penetration()
    assert result.basis.value == "CASH"
    assert result.status.value == "COMPLETE"
