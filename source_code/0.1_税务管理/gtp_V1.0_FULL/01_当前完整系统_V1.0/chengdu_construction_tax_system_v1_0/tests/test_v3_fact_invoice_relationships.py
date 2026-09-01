"""Task 07b red/void/correction relationship PostgreSQL contracts."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.invoice.relationships import (
    InvoiceProjectionValue,
    link_facts,
    project_effective_invoice_totals,
    record_void_event,
)
from app.v3_fact_models import Fact, FactRelationship, InvoiceFact
from app.v3_party_models import InternalEntity, Party

ROOT = Path(__file__).resolve().parents[1]


def _internal_party_ids(session: Session) -> tuple[int, int]:
    rows = session.execute(
        select(InternalEntity.party_id, InternalEntity.canonical_code)
        .order_by(InternalEntity.party_id)
    ).all()
    if len(rows) >= 2:
        return int(rows[0][0]), int(rows[1][0])

    used_codes = {str(row[1]) for row in rows}
    created: list[int] = [int(row[0]) for row in rows]
    for canonical in ("A08", "B01", "C01", "D01"):
        if canonical in used_codes:
            continue
        party = Party(
            code=f"T07B_{canonical}",
            name=f"Task07b {canonical}",
            short_name=canonical,
            party_type="internal",
            active=True,
        )
        session.add(party)
        session.flush()
        session.add(
            InternalEntity(
                party_id=party.id,
                canonical_code=canonical,
                business_role=canonical[0],
                legal_entity=True,
            )
        )
        session.flush()
        created.append(int(party.id))
        if len(created) >= 2:
            break
    assert len(created) >= 2
    return created[0], created[1]


def _invoice(
    session: Session,
    *,
    seller_id: int,
    buyer_id: int,
    key: str,
    status: str,
    net: str,
    vat: str,
    gross: str,
) -> Fact:
    fact = Fact(
        fact_type="INVOICE",
        business_identity_key=f"INVOICE|{key}",
        validation_status="VALID",
    )
    session.add(fact)
    session.flush()
    session.add(
        InvoiceFact(
            fact_id=fact.id,
            seller_party_id=seller_id,
            buyer_party_id=buyer_id,
            invoice_identity_key=key,
            invoice_identity_version="DIGITAL_V1",
            invoice_number=key.rsplit("|", 1)[-1],
            invoice_medium="DIGITAL",
            invoice_category="SPECIAL",
            invoice_status=status,
            net_amount=Decimal(net),
            vat_amount=Decimal(vat),
            gross_amount=Decimal(gross),
            currency="CNY",
        )
    )
    session.flush()
    return fact


def test_task07b_schema_contract_and_migration_chain():
    table = FactRelationship.__table__
    fk_targets = {
        element.target_fullname
        for constraint in table.foreign_key_constraints
        for element in constraint.elements
    }
    assert fk_targets == {"facts.id"}

    invoice_columns = set(InvoiceFact.__table__.c.keys())
    assert {"invoice_medium", "invoice_category", "invoice_status"} <= invoice_columns
    assert {"red_blue_flag", "original_invoice_id"}.isdisjoint(invoice_columns)

    migration = (
        ROOT / "alembic" / "versions" / "78_v3_invoice_fact_relationships.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "77_v3_fact_core_invoice"' in migration
    upper = migration.upper()
    assert "REVERSAL_OF" in upper
    assert "VOID_RELATION" in upper
    assert "DROP TABLE INVOICES" not in upper


def test_blue_plus_red_projects_to_zero_and_keeps_two_facts(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            seller_id, buyer_id = _internal_party_ids(session)
            blue = _invoice(
                session,
                seller_id=seller_id,
                buyer_id=buyer_id,
                key="DIGITAL_V1|T07B-BLUE-001",
                status="VALID",
                net="100.00",
                vat="13.00",
                gross="113.00",
            )
            red = _invoice(
                session,
                seller_id=seller_id,
                buyer_id=buyer_id,
                key="DIGITAL_V1|T07B-RED-001",
                status="RED",
                net="-100.00",
                vat="-13.00",
                gross="-113.00",
            )
            relationship = link_facts(
                session,
                source_fact_id=red.id,
                target_fact_id=blue.id,
                relationship_type="REVERSAL_OF",
                reason="full red reversal contract test",
            )

            totals = project_effective_invoice_totals(
                (
                    InvoiceProjectionValue("VALID", Decimal("100"), Decimal("13"), Decimal("113")),
                    InvoiceProjectionValue("RED", Decimal("-100"), Decimal("-13"), Decimal("-113")),
                )
            )
            assert totals == (Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))
            assert session.scalar(
                select(func.count()).select_from(Fact).where(Fact.id.in_([blue.id, red.id]))
            ) == 2
            assert relationship.relationship_type == "REVERSAL_OF"
        finally:
            session.close()
            tx.rollback()


def test_void_event_excludes_projection_without_zeroing_amount(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            seller_id, buyer_id = _internal_party_ids(session)
            invoice = _invoice(
                session,
                seller_id=seller_id,
                buyer_id=buyer_id,
                key="DIGITAL_V1|T07B-VOID-001",
                status="VALID",
                net="200.00",
                vat="26.00",
                gross="226.00",
            )
            relation = record_void_event(
                session,
                target_invoice_fact_id=invoice.id,
                event_business_identity_key="INVOICE_VOID|T07B-VOID-001",
                reason="tax bureau void confirmation",
            )
            row = session.get(InvoiceFact, invoice.id)
            assert row is not None
            assert row.invoice_status == "VOIDED"
            assert row.net_amount == Decimal("200.00")
            assert row.vat_amount == Decimal("26.00")
            assert row.gross_amount == Decimal("226.00")
            assert relation.relationship_type == "VOID_RELATION"
            assert project_effective_invoice_totals(
                (InvoiceProjectionValue("VOIDED", row.net_amount, row.vat_amount, row.gross_amount),)
            ) == (Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))
        finally:
            session.close()
            tx.rollback()


def test_postgresql_rejects_positive_red_amount(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            seller_id, buyer_id = _internal_party_ids(session)
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    _invoice(
                        session,
                        seller_id=seller_id,
                        buyer_id=buyer_id,
                        key="DIGITAL_V1|T07B-BAD-RED-001",
                        status="RED",
                        net="100.00",
                        vat="13.00",
                        gross="113.00",
                    )
        finally:
            session.close()
            tx.rollback()


def test_postgresql_rejects_self_and_duplicate_relationships(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            seller_id, buyer_id = _internal_party_ids(session)
            blue = _invoice(
                session,
                seller_id=seller_id,
                buyer_id=buyer_id,
                key="DIGITAL_V1|T07B-BLUE-REL-001",
                status="VALID",
                net="100.00",
                vat="13.00",
                gross="113.00",
            )
            red = _invoice(
                session,
                seller_id=seller_id,
                buyer_id=buyer_id,
                key="DIGITAL_V1|T07B-RED-REL-001",
                status="RED",
                net="-50.00",
                vat="-6.50",
                gross="-56.50",
            )
            link_facts(
                session,
                source_fact_id=red.id,
                target_fact_id=blue.id,
                relationship_type="REVERSAL_OF",
            )
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        FactRelationship(
                            source_fact_id=red.id,
                            target_fact_id=blue.id,
                            relationship_type="REVERSAL_OF",
                        )
                    )
                    session.flush()
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        FactRelationship(
                            source_fact_id=blue.id,
                            target_fact_id=blue.id,
                            relationship_type="CORRECTS",
                        )
                    )
                    session.flush()
        finally:
            session.close()
            tx.rollback()
