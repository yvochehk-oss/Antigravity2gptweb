"""Task15 Entity Tax Ledger builder PLAN/APPLY PostgreSQL contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db import engine
from app.models import (
    EntityTaxLedger,
    EntityTaxLedgerComponent,
    EntityTaxManagementInput,
)
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_vat_ledger_models import EntityVatLedger
from app.v3_vat_review_models import VatOutputPeriodAssertion
from scripts.v3.entity_tax_ledger import (
    _load_plan,
    apply_plan,
    build_one,
    make_plan,
)


ROOT = Path(__file__).resolve().parents[1]
PERIOD = date(2026, 8, 1)


def _suffix() -> str:
    return uuid.uuid4().hex[:8].upper()


def _new_entity(
    session: Session,
    *,
    suffix: str | None = None,
    legal_entity: bool = True,
    active: bool = True,
) -> InternalEntity:
    suffix = suffix or _suffix()
    party = Party(
        code=f"T15-P-{suffix}",
        name=f"Task15 Party {suffix}",
        short_name="T15",
        party_type="internal",
        active=True,
    )
    session.add(party)
    session.flush()
    entity = InternalEntity(
        party_id=party.id,
        canonical_code=f"T15{suffix}",
        business_role="A",
        legal_entity=legal_entity,
        active=active,
    )
    session.add(entity)
    session.flush()
    return entity


def _seed_vat(
    session: Session,
    entity: InternalEntity,
    *,
    period: date = PERIOD,
    marker: str = "A",
    add_state: bool = True,
) -> EntityVatLedger:
    now = datetime.now(timezone.utc)
    run = CalculationRun(
        reporting_party_id=entity.party_id,
        tax_type="VAT",
        tax_period=period,
        run_kind="STANDARD",
        run_status="DRAFT",
        ruleset_version=f"TASK15_VAT_{marker}",
        input_snapshot_sha256=(marker.lower() * 64)[:64],
        created_by="pytest",
    )
    session.add(run)
    session.flush()
    run.result_sha256=(marker.lower() * 64)[:64]
    run.run_status="SUCCEEDED"
    run.completed_at=now
    session.flush()
    if add_state:
        session.add(
            TaxPeriodState(
                reporting_party_id=entity.party_id,
                tax_type="VAT",
                tax_period=period,
                state="OPEN",
                current_run_id=run.id,
            )
        )
        session.flush()
        session.add(
            VatOutputPeriodAssertion(
                reporting_party_id=entity.party_id,
                tax_period=period,
                asserted_output_vat_total=Decimal("0.00"),
                source="Task15 reviewed zero VAT output",
                reviewed=True,
                reviewed_by="pytest",
                reviewed_at=now,
            )
        )
        session.flush()
    ledger = EntityVatLedger(
        calculation_run_id=run.id,
        reporting_party_id=entity.party_id,
        tax_period=period,
        opening_input_credit=Decimal("0.00"),
        output_vat=Decimal("0.00"),
        input_vat=Decimal("0.00"),
        tax_prepayment=Decimal("0.00"),
        vat_payable_before_prepayment=Decimal("0.00"),
        closing_input_credit=Decimal("0.00"),
        vat_payable_after_prepayment=Decimal("0.00"),
        unapplied_tax_prepayment=Decimal("0.00"),
    )
    session.add(ledger)
    session.flush()
    return ledger


def _seed_inputs(
    session: Session,
    entity: InternalEntity,
    *,
    revenue: str | Decimal | None = "100.00",
    real_cost: str | Decimal | None = "40.00",
    revenue_version: int = 1,
    period: date = PERIOD,
) -> list[EntityTaxManagementInput]:
    now = datetime.now(timezone.utc)
    rows: list[EntityTaxManagementInput] = []
    if revenue is not None:
        rows.append(
            EntityTaxManagementInput(
                reporting_party_id=entity.party_id,
                tax_period=period,
                input_type="REVENUE",
                input_version=revenue_version,
                amount=Decimal(str(revenue)),
                source="Task15 reviewed monthly management schedule",
                reviewed=True,
                reviewed_by="pytest",
                reviewed_at=now,
            )
        )
    if real_cost is not None:
        rows.append(
            EntityTaxManagementInput(
                reporting_party_id=entity.party_id,
                tax_period=period,
                input_type="REAL_COST",
                input_version=1,
                amount=Decimal(str(real_cost)),
                source="Task15 reviewed monthly management schedule",
                reviewed=True,
                reviewed_by="pytest",
                reviewed_at=now,
            )
        )
    session.add_all(rows)
    session.flush()
    return rows


def _setup(
    *,
    with_vat: bool = True,
    revenue: str | Decimal | None = "100.00",
    real_cost: str | Decimal | None = "40.00",
    legal_entity: bool = True,
    active: bool = True,
    period: date = PERIOD,
) -> tuple[str, int, int | None]:
    with Session(engine) as session:
        entity = _new_entity(session, legal_entity=legal_entity, active=active)
        vat_id: int | None = None
        if with_vat:
            vat_id = int(_seed_vat(session, entity, period=period).id)
        _seed_inputs(
            session,
            entity,
            revenue=revenue,
            real_cost=real_cost,
            period=period,
        )
        session.commit()
        return entity.canonical_code, int(entity.party_id), vat_id


def _count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_standard_build_writes_three_typed_components_and_uses_official_vat(seeded_app):
    entity_code, party_id, vat_id = _setup()
    with Session(engine) as session:
        result = build_one(
            session,
            entity_code=entity_code,
            period="2026-08",
            created_by="pytest",
        )
        session.commit()
        assert result["status"] == "BUILT"
        assert result["run_kind"] == "STANDARD"
        ledger = session.get(EntityTaxLedger, result["ledger_id"])
        assert ledger is not None
        assert ledger.reporting_party_id == party_id
        assert ledger.entity_vat_ledger_id == vat_id
        assert ledger.revenue == Decimal("100.00")
        assert ledger.real_cost == Decimal("40.00")
        assert ledger.estimated_profit == Decimal("60.00")
        assert ledger.estimated_cit == Decimal("15.00")
        components = session.scalars(
            select(EntityTaxLedgerComponent)
            .where(EntityTaxLedgerComponent.ledger_id == ledger.id)
            .order_by(EntityTaxLedgerComponent.component_type)
        ).all()
        assert [row.component_type for row in components] == [
            "ESTIMATED_CIT",
            "REAL_COST",
            "REVENUE",
        ]
        assert all(row.management_input_id is not None for row in components if row.component_type != "ESTIMATED_CIT")
        assert next(row for row in components if row.component_type == "ESTIMATED_CIT").management_input_id is None


def test_plan_is_read_only_and_contains_scope_sources_state_and_digest(seeded_app):
    entity_code, _, _ = _setup()
    with Session(engine) as session:
        before = (
            _count(session, CalculationRun),
            _count(session, TaxPeriodState),
            _count(session, EntityTaxLedger),
            _count(session, EntityTaxLedgerComponent),
        )
        plan = make_plan(session, entity_code=entity_code, period="2026-08")
        after = (
            _count(session, CalculationRun),
            _count(session, TaxPeriodState),
            _count(session, EntityTaxLedger),
            _count(session, EntityTaxLedgerComponent),
        )
        assert before == after
        assert plan["alembic_head"] == "87_v3_entity_tax_ledgers"
        assert plan["period_state"]["state"] is None
        assert plan["source_snapshot"]["official_vat"]["id"]
        assert plan["calculation"]["estimated_cit"] == "15.00"
        assert len(plan["plan_digest"]) == 64


def test_plan_digest_tampering_is_rejected(tmp_path, seeded_app):
    entity_code, _, _ = _setup()
    with Session(engine) as session:
        plan = make_plan(session, entity_code=entity_code, period=PERIOD)
    path = tmp_path / "task15-plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    tampered = dict(plan)
    tampered["calculation"] = dict(plan["calculation"])
    tampered["calculation"]["revenue"] = "999.99"
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_digest"):
        _load_plan(path)


def test_stale_plan_rejects_changed_reviewed_input(seeded_app):
    entity_code, party_id, _ = _setup()
    with Session(engine) as session:
        plan = make_plan(session, entity_code=entity_code, period=PERIOD)
    with Session(engine) as session:
        revenue = session.scalar(
            select(EntityTaxManagementInput).where(
                EntityTaxManagementInput.reporting_party_id == party_id,
                EntityTaxManagementInput.input_type == "REVENUE",
                EntityTaxManagementInput.tax_period == PERIOD,
            )
        )
        assert revenue is not None
        revenue.amount = Decimal("101.00")
        session.commit()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="stale Task15 plan"):
            apply_plan(session, plan, created_by="pytest")
        session.rollback()


def test_repeated_same_snapshot_is_idempotent_without_duplicate_rows(seeded_app):
    entity_code, party_id, _ = _setup()
    with Session(engine) as session:
        first = build_one(session, entity_code=entity_code, period=PERIOD, created_by="pytest")
        session.commit()
    with Session(engine) as session:
        second = build_one(session, entity_code=entity_code, period=PERIOD, created_by="pytest")
        session.commit()
        assert second["status"] == "NO_CHANGE"
        assert second["ledger_id"] == first["ledger_id"]
        assert session.scalar(
            select(func.count()).select_from(EntityTaxLedger).where(
                EntityTaxLedger.reporting_party_id == party_id,
                EntityTaxLedger.tax_period == PERIOD,
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(EntityTaxLedgerComponent).where(
                EntityTaxLedgerComponent.ledger_id == first["ledger_id"],
            )
        ) == 3


@pytest.mark.parametrize(
    "setup_kwargs, expected",
    [
        ({"real_cost": None}, "REAL_COST"),
        ({"with_vat": False}, "EntityVatLedger"),
    ],
)
def test_missing_required_evidence_fails_closed(seeded_app, setup_kwargs, expected):
    entity_code, _, _ = _setup(**setup_kwargs)
    with Session(engine) as session:
        with pytest.raises(ValueError, match=expected):
            build_one(session, entity_code=entity_code, period=PERIOD, created_by="pytest")
        session.rollback()


def test_missing_cit_rule_fails_closed_without_defaulting_rate(seeded_app):
    entity_code, _, _ = _setup()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="tax rule"):
            make_plan(
                session,
                entity_code=entity_code,
                period=PERIOD,
                cit_rule_code="CIT_DOES_NOT_EXIST",
            )


def test_wrong_entity_scope_and_nonlegal_entity_fail_closed(seeded_app):
    target_code, _, _ = _setup(revenue=None, real_cost=None)
    with Session(engine) as session:
        other = _new_entity(session)
        _seed_inputs(session, other)
        session.commit()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="REVENUE"):
            make_plan(session, entity_code=target_code, period=PERIOD)

    nonlegal_code, _, _ = _setup(legal_entity=False, with_vat=False, revenue=None, real_cost=None)
    with Session(engine) as session:
        with pytest.raises(ValueError, match="not a legal entity"):
            make_plan(session, entity_code=nonlegal_code, period=PERIOD)


def test_only_current_vat_period_run_is_accepted(seeded_app):
    entity_code, party_id, first_vat_id = _setup()
    with Session(engine) as session:
        entity = session.scalar(select(InternalEntity).where(InternalEntity.canonical_code == entity_code))
        assert entity is not None
        second = _seed_vat(session, entity, marker="B", add_state=False)
        state = session.scalar(
            select(TaxPeriodState).where(
                TaxPeriodState.reporting_party_id == party_id,
                TaxPeriodState.tax_type == "VAT",
                TaxPeriodState.tax_period == PERIOD,
            )
        )
        assert state is not None
        state.current_run_id = second.calculation_run_id
        second_vat_id = int(second.id)
        session.commit()
    with Session(engine) as session:
        plan = make_plan(session, entity_code=entity_code, period=PERIOD)
        assert plan["source_snapshot"]["official_vat"]["id"] == second_vat_id
        assert plan["source_snapshot"]["official_vat"]["id"] != first_vat_id


def test_closed_period_requires_restatement_and_supersedes_current_run_reusing_vat(seeded_app):
    entity_code, party_id, vat_id = _setup()
    with Session(engine) as session:
        first = build_one(session, entity_code=entity_code, period=PERIOD, created_by="pytest")
        session.commit()
    with Session(engine) as session:
        state = session.scalar(
            select(TaxPeriodState).where(
                TaxPeriodState.reporting_party_id == party_id,
                TaxPeriodState.tax_type == "ENTITY_TAX",
                TaxPeriodState.tax_period == PERIOD,
            )
        )
        assert state is not None
        state.state = "CLOSED"
        state.closed_run_id = state.current_run_id
        state.closed_by = "pytest"
        state.closed_at = datetime.now(timezone.utc)
        session.commit()
    with Session(engine) as session:
        entity = session.scalar(select(InternalEntity).where(InternalEntity.canonical_code == entity_code))
        assert entity is not None
        _seed_inputs(session, entity, revenue="120.00", real_cost=None, revenue_version=2)
        session.commit()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="CLOSED"):
            build_one(session, entity_code=entity_code, period=PERIOD, created_by="pytest")
        session.rollback()
    with Session(engine) as session:
        replacement = build_one(
            session,
            entity_code=entity_code,
            period=PERIOD,
            created_by="pytest",
            allow_restatement=True,
        )
        session.commit()
        assert replacement["run_kind"] == "RESTATEMENT"
        assert replacement["supersedes_run_id"] == first["calculation_run_id"]
        assert replacement["result"]["revenue"] == "120.00"
        assert replacement["result"]["estimated_cit"] == "20.00"
        assert replacement["source_snapshot"]["official_vat"]["id"] == vat_id
        state = session.scalar(
            select(TaxPeriodState).where(
                TaxPeriodState.reporting_party_id == party_id,
                TaxPeriodState.tax_type == "ENTITY_TAX",
                TaxPeriodState.tax_period == PERIOD,
            )
        )
        assert state is not None
        assert state.state == "CLOSED"
        assert state.closed_run_id == first["calculation_run_id"]
        assert state.current_run_id == replacement["calculation_run_id"]
        assert session.scalar(
            select(func.count()).select_from(EntityTaxLedger).where(
                EntityTaxLedger.reporting_party_id == party_id,
                EntityTaxLedger.tax_period == PERIOD,
            )
        ) == 2


def test_failed_apply_transaction_rolls_back_run_state_ledger_and_components(seeded_app, monkeypatch):
    entity_code, party_id, _ = _setup()
    with Session(engine) as session:
        before = (
            _count(session, EntityTaxLedger),
            _count(session, EntityTaxLedgerComponent),
            _count(session, CalculationRun),
            _count(session, TaxPeriodState),
        )
        calls = 0
        original_flush = session.flush

        def fail_on_component_flush(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 5:
                raise RuntimeError("injected component flush failure")
            return original_flush(*args, **kwargs)

        monkeypatch.setattr(session, "flush", fail_on_component_flush)
        with pytest.raises(RuntimeError, match="injected"):
            build_one(session, entity_code=entity_code, period=PERIOD, created_by="pytest")
        session.rollback()
    with Session(engine) as session:
        assert (
            _count(session, EntityTaxLedger),
            _count(session, EntityTaxLedgerComponent),
            _count(session, CalculationRun),
            _count(session, TaxPeriodState),
        ) == before
        assert session.scalar(
            select(EntityTaxManagementInput.id).where(
                EntityTaxManagementInput.reporting_party_id == party_id,
            )
        ) is not None


def test_plan_and_apply_require_exact_database_confirmation_and_head(
    seeded_app, tmp_path, monkeypatch, postgres_test_database_url
):
    entity_code, _, _ = _setup()
    with Session(engine) as session:
        plan = make_plan(session, entity_code=entity_code, period=PERIOD)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    from scripts.v3 import entity_tax_ledger as builder

    monkeypatch.setenv("DATABASE_URL", postgres_test_database_url)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "entity_tax_ledger.py",
            "--apply",
            "--plan",
            str(path),
            "--confirm-database",
            "projectrag",
            "--created-by",
            "pytest",
        ],
    )
    with pytest.raises(SystemExit, match="confirm-database"):
        builder.main()


def test_cli_plan_json_then_confirmed_apply_json(
    seeded_app, tmp_path, monkeypatch, postgres_test_database_url
):
    entity_code, _, _ = _setup()
    from scripts.v3 import entity_tax_ledger as builder

    plan_path = tmp_path / "cli-plan.json"
    result_path = tmp_path / "cli-result.json"
    database_name = make_url(postgres_test_database_url).database
    assert database_name
    monkeypatch.setenv("DATABASE_URL", postgres_test_database_url)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "entity_tax_ledger.py",
            "--entity",
            entity_code,
            "--period",
            "2026-08",
            "--json",
            str(plan_path),
        ],
    )
    assert builder.main() == 0
    plan = _load_plan(plan_path)
    assert plan["database"] == database_name

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "entity_tax_ledger.py",
            "--apply",
            "--plan",
            str(plan_path),
            "--confirm-database",
            database_name,
            "--created-by",
            "pytest",
            "--json",
            str(result_path),
        ],
    )
    assert builder.main() == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "BUILT"
    assert result["plan_digest"] == plan["plan_digest"]
