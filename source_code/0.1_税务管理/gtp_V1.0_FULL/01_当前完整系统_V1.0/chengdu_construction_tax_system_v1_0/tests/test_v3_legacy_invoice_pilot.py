"""Task 08 deterministic legacy invoice pilot contracts."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.invoice.legacy_migration import (
    MIGRATION_IDENTITY_VERSION,
    LegacyInvoiceSnapshot,
    PartyRef,
    plan_pilot,
)
from app.models import Invoice, Project, RealCost
from app.v3_fact_models import Fact, InvoiceFact, LegacyInvoiceMap
from app.v3_party_models import InternalEntity, Party
from scripts.v3.invoice_legacy_pilot import (
    _apply_plan,
    _build_plan_wrapper,
    _fetch_all_unmapped_rows,
)

ROOT = Path(__file__).resolve().parents[1]


def _row(
    row_id: int,
    *,
    entity: str,
    counterparty: str,
    direction: str,
    invoice_no: str = "INV-001",
    net: str = "100.00",
    vat: str = "13.00",
    rate: str = "0.13",
) -> LegacyInvoiceSnapshot:
    return LegacyInvoiceSnapshot(
        id=row_id,
        project_id=1,
        invoice_no=invoice_no,
        period="2026-08",
        entity_code=entity,
        direction=direction,
        counterparty_code=counterparty,
        category="material",
        net=Decimal(net),
        vat=Decimal(vat),
        rate=Decimal(rate),
        deductible=True,
        note="",
    )


def _lookup() -> dict[str, PartyRef]:
    return {
        "A08": PartyRef(1, "A08", "internal"),
        "B01": PartyRef(2, "B01", "internal"),
        "EXT-X": PartyRef(3, "EXT-X", "external"),
    }


def test_exact_internal_in_out_pair_becomes_one_merge_action():
    plan = plan_pilot(
        (
            _row(10, entity="A08", counterparty="B01", direction="out"),
            _row(11, entity="B01", counterparty="A08", direction="in"),
        ),
        _lookup(),
    )
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.action == "MERGE_PAIR"
    assert action.legacy_ids == (10, 11)
    assert action.migration_status == "MERGED"
    assert action.seller_party_id == 1
    assert action.buyer_party_id == 2
    assert action.identity_key == f"{MIGRATION_IDENTITY_VERSION}|PAIR|10-11"


def test_single_perspective_is_migrated_but_needs_review_semantically():
    plan = plan_pilot(
        (_row(20, entity="A08", counterparty="EXT-X", direction="in"),),
        _lookup(),
    )
    action = plan.actions[0]
    assert action.action == "MIGRATE_SINGLE"
    assert action.migration_status == "MIGRATED_SINGLE_PERSPECTIVE"
    assert "REQUIRES_REVIEW" in action.reason


def test_conflicting_or_duplicate_coarse_identity_never_auto_merges():
    plan = plan_pilot(
        (
            _row(30, entity="A08", counterparty="B01", direction="out"),
            _row(
                31,
                entity="B01",
                counterparty="A08",
                direction="in",
                vat="9.00",
                rate="0.09",
            ),
        ),
        _lookup(),
    )
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.action == "REVIEW_ONLY"
    assert action.legacy_ids == (30, 31)
    assert action.migration_status == "NEEDS_REVIEW"
    assert "CONFLICTING" in action.reason


def test_unresolved_party_is_covered_as_review_only_not_dropped():
    plan = plan_pilot(
        (_row(40, entity="A08", counterparty="UNKNOWN-X", direction="out"),),
        _lookup(),
    )
    assert plan.covered_ids == (40,)
    action = plan.actions[0]
    assert action.action == "REVIEW_ONLY"
    assert action.migration_status == "NEEDS_REVIEW"
    assert action.reason.startswith("UNRESOLVED_PARTY:")


def test_blank_invoice_number_is_review_only_and_never_creates_fact_identity():
    plan = plan_pilot(
        (
            _row(50, entity="A08", counterparty="B01", direction="out", invoice_no=""),
            _row(51, entity="B01", counterparty="A08", direction="in", invoice_no=""),
        ),
        _lookup(),
    )
    assert [action.action for action in plan.actions] == ["REVIEW_ONLY", "REVIEW_ONLY"]
    assert {action.reason for action in plan.actions} == {"MISSING_INVOICE_NUMBER"}
    assert all(action.identity_key is None for action in plan.actions)


def test_revision_79_is_additive_nonunique_bridge():
    migration = (
        ROOT / "alembic" / "versions" / "79_v3_legacy_invoice_pilot_bridge.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "78_v3_invoice_fact_relationships"' in migration
    upper = migration.upper()
    assert "INVOICE_FACT_ID" in upper
    assert "DROP TABLE REAL_COST_INVOICE_LINKS" not in upper
    assert "UNIQUE=FALSE" in upper.replace(" ", "")


def _ensure_internal_pair(session: Session) -> tuple[Party, Party]:
    existing = session.execute(
        select(InternalEntity, Party)
        .join(Party, Party.id == InternalEntity.party_id)
        .order_by(InternalEntity.party_id)
        .limit(2)
    ).all()
    if len(existing) >= 2:
        return existing[0][1], existing[1][1]

    used = {
        str(value)
        for value in session.execute(select(InternalEntity.canonical_code)).scalars().all()
    }
    created: list[Party] = [row[1] for row in existing]
    for canonical in ("A08", "B01", "C01", "D01"):
        if canonical in used:
            continue
        party = Party(
            code=f"T08_{canonical}",
            name=f"Task08 {canonical}",
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
                active=True,
            )
        )
        session.flush()
        created.append(party)
        used.add(canonical)
        if len(created) >= 2:
            break
    assert len(created) >= 2
    return created[0], created[1]


def _project(session: Session) -> Project:
    project = session.scalar(select(Project).order_by(Project.id).limit(1))
    if project is not None:
        return project
    project = Project(
        code="T08-PROJECT",
        name="Task08 Project",
        city="Chengdu",
        contract_total=Decimal("1000000.00"),
        tax_method="general",
    )
    session.add(project)
    session.flush()
    return project


def test_postgresql_pair_pilot_maps_two_rows_to_one_fact_and_bridges_real_cost(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            assert session.scalar(select(func.count()).select_from(LegacyInvoiceMap)) == 0
            seller, buyer = _ensure_internal_pair(session)
            seller_code = session.scalar(
                select(InternalEntity.canonical_code).where(InternalEntity.party_id == seller.id)
            )
            buyer_code = session.scalar(
                select(InternalEntity.canonical_code).where(InternalEntity.party_id == buyer.id)
            )
            assert seller_code and buyer_code
            project = _project(session)

            out_row = Invoice(
                project_id=project.id,
                invoice_no="T08-PAIR-001",
                period="2026-08",
                entity_code=seller_code,
                direction="out",
                counterparty_code=buyer_code,
                category="material",
                net=Decimal("100.00"),
                vat=Decimal("13.00"),
                rate=Decimal("0.13"),
                deductible=False,
                note="seller perspective",
            )
            in_row = Invoice(
                project_id=project.id,
                invoice_no="T08-PAIR-001",
                period="2026-08",
                entity_code=buyer_code,
                direction="in",
                counterparty_code=seller_code,
                category="material",
                net=Decimal("100.00"),
                vat=Decimal("13.00"),
                rate=Decimal("0.13"),
                deductible=True,
                note="buyer perspective",
            )
            session.add_all([out_row, in_row])
            session.flush()

            real_cost = RealCost(
                project_id=project.id,
                entity_code=buyer_code,
                counterparty_code=seller_code,
                category="material",
                subcategory="",
                period="2026-08",
                amount=Decimal("100.00"),
                external_cash=False,
                note="Task08 provenance",
            )
            session.add(real_cost)
            session.flush()
            session.execute(
                text(
                    """
                    INSERT INTO real_cost_invoice_links(
                        real_cost_id, invoice_id, source, source_record_id
                    ) VALUES (:real_cost_id, :invoice_id, 'test', 'T08')
                    """
                ),
                {"real_cost_id": real_cost.id, "invoice_id": in_row.id},
            )
            session.flush()

            all_rows = _fetch_all_unmapped_rows(conn)
            wrapper = _build_plan_wrapper(
                conn,
                all_rows,
                explicit_ids={int(out_row.id), int(in_row.id)},
            )
            assert len(wrapper["plan"]["actions"]) == 1
            assert wrapper["plan"]["actions"][0]["action"] == "MERGE_PAIR"

            result = _apply_plan(conn, wrapper)
            assert result["selected_count"] == 2
            assert result["created_fact_count"] == 1
            fact_id = int(result["created_fact_ids"][0])

            maps = session.execute(
                select(LegacyInvoiceMap)
                .where(LegacyInvoiceMap.legacy_invoice_id.in_([out_row.id, in_row.id]))
                .order_by(LegacyInvoiceMap.legacy_invoice_id)
            ).scalars().all()
            assert len(maps) == 2
            assert {item.migration_status for item in maps} == {"MERGED"}
            assert {item.invoice_fact_id for item in maps} == {fact_id}

            fact = session.get(Fact, fact_id)
            invoice_fact = session.get(InvoiceFact, fact_id)
            assert fact is not None and fact.validation_status == "NEEDS_REVIEW"
            assert invoice_fact is not None
            assert invoice_fact.invoice_identity_version == MIGRATION_IDENTITY_VERSION
            assert invoice_fact.net_amount == Decimal("100.00")
            assert invoice_fact.vat_amount == Decimal("13.00")
            assert invoice_fact.gross_amount == Decimal("113.00")

            bridge = session.execute(
                text(
                    "SELECT invoice_fact_id FROM real_cost_invoice_links "
                    "WHERE real_cost_id=:id"
                ),
                {"id": real_cost.id},
            ).scalar_one()
            assert int(bridge) == fact_id
        finally:
            session.close()
            tx.rollback()
