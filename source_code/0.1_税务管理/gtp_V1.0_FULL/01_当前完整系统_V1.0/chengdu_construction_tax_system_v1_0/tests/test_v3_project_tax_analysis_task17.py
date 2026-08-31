"""Task17 Project Tax Analysis contracts and PostgreSQL integration tests."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.tax.project_tax_analysis import ProjectAllocationError, allocate_vat_event, allocation_coverage
from app.models import Project
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_project_analysis_models import FactProjectAllocation, ProjectTaxAnalysis
from app.v3_vat_ledger_models import OutputVatEvent
from scripts.v3.project_allocation import review_allocation
from scripts.v3.project_tax_analysis import build_one, make_plan

ROOT = Path(__file__).resolve().parents[1]


def _allocation(*, project_id: int, net: str, vat: str, gross: str, allocation_id: int | None = None):
    return {"id": allocation_id, "fact_id": 1, "project_id": project_id, "allocated_net": Decimal(net), "allocated_vat": Decimal(vat), "allocated_gross": Decimal(gross), "status": "CONFIRMED", "is_current": True}


def test_revision_88_is_additive_and_keeps_project_out_of_entity_ledgers():
    migration = (ROOT / "alembic" / "versions" / "88_v3_project_tax_analysis.py").read_text(encoding="utf-8")
    assert 'down_revision = "87_v3_entity_tax_ledgers"' in migration
    assert '"fact_project_allocations"' in migration
    assert '"project_tax_analysis"' in migration
    assert '"project_tax_analysis_components"' in migration
    assert "ALTER TABLE entity_vat_ledgers" not in migration
    assert "ALTER TABLE entity_tax_ledgers" not in migration
    assert "AI project-allocation proposals must be inserted as CANDIDATE" in migration


def test_full_allocation_reconciles_signed_invoice_amounts():
    coverage = allocation_coverage(source_net=Decimal("-100.00"), source_vat=Decimal("-13.00"), source_gross=Decimal("-113.00"), allocations=[_allocation(project_id=1, net="-60.00", vat="-7.80", gross="-67.80"), _allocation(project_id=2, net="-40.00", vat="-5.20", gross="-45.20")])
    assert coverage.status == "FULL"
    assert coverage.remaining_gross == Decimal("0.00")


def test_partial_allocation_stays_partial_instead_of_redistributing_event():
    allocations = [_allocation(project_id=1, net="60.00", vat="7.80", gross="67.80", allocation_id=1)]
    coverage = allocation_coverage(source_net=Decimal("100.00"), source_vat=Decimal("13.00"), source_gross=Decimal("113.00"), allocations=allocations)
    assert coverage.status == "PARTIAL"
    shares = allocate_vat_event(event_amount=Decimal("13.00"), source_vat=Decimal("13.00"), source_net=Decimal("100.00"), allocations=allocations)
    assert len(shares) == 1
    assert shares[0].tax_amount == Decimal("7.80")
    assert shares[0].taxable_amount == Decimal("60.00")


def test_overallocation_is_explicit():
    coverage = allocation_coverage(source_net=Decimal("100.00"), source_vat=Decimal("13.00"), source_gross=Decimal("113.00"), allocations=[_allocation(project_id=1, net="70.00", vat="9.10", gross="79.10"), _allocation(project_id=2, net="50.00", vat="6.50", gross="56.50")])
    assert coverage.status == "OVER"


def test_allocation_sign_conflict_is_rejected():
    with pytest.raises(ProjectAllocationError):
        allocation_coverage(source_net=Decimal("-100.00"), source_vat=Decimal("-13.00"), source_gross=Decimal("-113.00"), allocations=[_allocation(project_id=1, net="100.00", vat="13.00", gross="113.00")])


def _create_project(session: Session, code: str, *, entity_code: str) -> Project:
    row = Project(code=code, name=f"Task17 {code}", city="成都", contract_total=Decimal("1000000.00"), tax_method="general", entity_code=entity_code)
    session.add(row)
    session.flush()
    return row


def _create_invoice_context(session: Session, suffix: str):
    entity = session.scalar(select(InternalEntity).where(InternalEntity.canonical_code == "A08"))
    assert entity is not None
    external = Party(code=f"T17-EXT-{suffix}", name=f"Task17 External {suffix}", short_name="T17EXT", party_type="external", active=True)
    session.add(external)
    session.flush()
    fact = Fact(fact_type="INVOICE", business_identity_key=f"INVOICE|DIGITAL_V1|T17-{suffix}", validation_status="VALID")
    session.add(fact)
    session.flush()
    invoice = InvoiceFact(fact_id=fact.id, seller_party_id=entity.party_id, buyer_party_id=external.id, invoice_identity_key=f"DIGITAL_V1|T17-{suffix}", invoice_identity_version="DIGITAL_V1", invoice_number=f"T17-{suffix}", invoice_date=date(2026, 1, 15), invoice_status="VALID", net_amount=Decimal("100.00"), vat_amount=Decimal("13.00"), gross_amount=Decimal("113.00"), currency="CNY")
    session.add(invoice)
    session.flush()
    return entity, fact, invoice


def test_postgresql_ai_cannot_insert_confirmed_allocation(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            entity, fact, _ = _create_invoice_context(session, "AI")
            project = _create_project(session, "T17-AI", entity_code=entity.canonical_code)
            nested = session.begin_nested()
            session.add(FactProjectAllocation(fact_id=fact.id, project_id=project.id, allocated_net=Decimal("100.00"), allocated_vat=Decimal("13.00"), allocated_gross=Decimal("113.00"), allocation_method="MANUAL", confidence="HIGH", status="CONFIRMED", proposal_source="AI", reviewed_by="pytest", reviewed_at=datetime.now(timezone.utc)))
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()


def test_postgresql_ai_candidate_can_be_human_confirmed(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            entity, fact, _ = _create_invoice_context(session, "AIREVIEW")
            project = _create_project(session, "T17-AIREVIEW", entity_code=entity.canonical_code)
            row = FactProjectAllocation(fact_id=fact.id, project_id=project.id, allocated_net=Decimal("100.00"), allocated_vat=Decimal("13.00"), allocated_gross=Decimal("113.00"), allocation_method="MANUAL", confidence="MEDIUM", status="CANDIDATE", proposal_source="AI")
            session.add(row)
            session.flush()
            result = review_allocation(session, allocation_id=row.id, decision="CONFIRMED", reviewed_by="pytest")
            assert result["status"] == "CONFIRMED"
            assert row.status == "CONFIRMED"
            assert row.reviewed_by == "pytest"
        finally:
            session.close()
            tx.rollback()


def test_project_tax_does_not_leak_other_projects(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            entity, fact, invoice = _create_invoice_context(session, "SPLIT")
            p1 = _create_project(session, "T17-P1", entity_code=entity.canonical_code)
            p2 = _create_project(session, "T17-P2", entity_code=entity.canonical_code)
            reviewed_at = datetime.now(timezone.utc)
            session.add_all([
                FactProjectAllocation(fact_id=fact.id, project_id=p1.id, allocated_net=Decimal("60.00"), allocated_vat=Decimal("7.80"), allocated_gross=Decimal("67.80"), allocation_method="EXPLICIT", confidence="HIGH", status="CONFIRMED", proposal_source="HUMAN", reviewed_by="pytest", reviewed_at=reviewed_at),
                FactProjectAllocation(fact_id=fact.id, project_id=p2.id, allocated_net=Decimal("40.00"), allocated_vat=Decimal("5.20"), allocated_gross=Decimal("45.20"), allocation_method="EXPLICIT", confidence="HIGH", status="CONFIRMED", proposal_source="HUMAN", reviewed_by="pytest", reviewed_at=reviewed_at),
                OutputVatEvent(invoice_fact_id=invoice.fact_id, reporting_party_id=entity.party_id, output_vat_period=date(2026, 2, 1), vat_amount=Decimal("13.00"), event_type="OUTPUT", event_status="CONFIRMED", evidence_type="MANUAL_REVIEW", confidence="HIGH", reviewed_by="pytest", reviewed_at=reviewed_at, source_system="pytest", external_event_id="T17-SPLIT-OUTPUT"),
            ])
            session.flush()
            plan = make_plan(session, entity_code="A08", period="2026-02")
            by_project = {row["project_id"]: row for row in plan["projects"]}
            assert Decimal(by_project[p1.id]["output_taxable_net"]) == Decimal("60.00")
            assert Decimal(by_project[p1.id]["output_vat"]) == Decimal("7.80")
            assert Decimal(by_project[p2.id]["output_taxable_net"]) == Decimal("40.00")
            assert Decimal(by_project[p2.id]["output_vat"]) == Decimal("5.20")
            result = build_one(session, entity_code="A08", period="2026-02", created_by="pytest", expected_input_snapshot_sha256=plan["input_snapshot_sha256"])
            assert result["status"] == "BUILT"
            rows = session.scalars(select(ProjectTaxAnalysis).where(ProjectTaxAnalysis.calculation_run_id == result["calculation_run_id"])).all()
            persisted = {row.project_id: row for row in rows}
            assert persisted[p1.id].output_vat == Decimal("7.80")
            assert persisted[p2.id].output_vat == Decimal("5.20")
            assert persisted[p1.id].output_vat != Decimal("13.00")
        finally:
            session.close()
            tx.rollback()
