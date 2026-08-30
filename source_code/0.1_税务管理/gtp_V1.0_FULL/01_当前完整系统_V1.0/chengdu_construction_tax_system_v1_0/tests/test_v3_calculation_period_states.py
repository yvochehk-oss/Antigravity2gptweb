"""Task13 CalculationRun / TaxPeriodState PostgreSQL contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import uuid

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.tax_periods import PeriodStateView, TaxPeriodStateError, assert_restatement_target, next_run_intent
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun, TaxPeriodState

ROOT = Path(__file__).resolve().parents[1]


def test_revision_83_chain_and_guard_triggers_are_explicit():
    migration = (ROOT / "alembic" / "versions" / "83_v3_calculation_runs_period_states.py").read_text(encoding="utf-8")
    assert 'down_revision = "82_v3_project_tax_prepayment"' in migration
    assert '"calculation_runs"' in migration
    assert '"tax_period_states"' in migration
    assert "trg_v3_guard_tax_period_state" in migration
    assert "trg_v3_guard_calculation_run_update" in migration
    assert "CLOSED tax period cannot be reopened" in migration
    assert "closed-period replacement requires RESTATEMENT" in migration


def test_period_state_schema_does_not_mix_project_cash_or_accrual_axes():
    prohibited = {"project_id", "cashflow_id", "bank_transaction_id", "invoice_fact_id", "recognized_revenue", "cost_amount"}
    assert prohibited.isdisjoint(CalculationRun.__table__.c.keys())
    assert prohibited.isdisjoint(TaxPeriodState.__table__.c.keys())
    assert {"reporting_party_id", "tax_type", "tax_period"}.issubset(CalculationRun.__table__.c.keys())
    assert {"reporting_party_id", "tax_type", "tax_period", "current_run_id", "closed_run_id"}.issubset(TaxPeriodState.__table__.c.keys())


def test_next_run_intent_switches_to_restatement_only_after_close():
    assert next_run_intent(None).run_kind == "STANDARD"
    assert next_run_intent(PeriodStateView("OPEN", 10, None)).run_kind == "STANDARD"
    intent = next_run_intent(PeriodStateView("CLOSED", 11, 10))
    assert intent.run_kind == "RESTATEMENT"
    assert intent.supersedes_run_id == 11
    with pytest.raises(TaxPeriodStateError):
        assert_restatement_target(PeriodStateView("CLOSED", 11, 10), 10)


def _new_internal_party(session: Session) -> int:
    suffix = uuid.uuid4().hex[:10].upper()
    party = Party(code=f"T13-{suffix}", name=f"Task13 {suffix}", short_name="T13", party_type="internal", active=True)
    session.add(party)
    session.flush()
    session.add(InternalEntity(party_id=party.id, canonical_code=f"T13{suffix[:8]}", business_role="A", legal_entity=True, active=True))
    session.flush()
    return party.id


def _succeeded_run(session: Session, *, party_id: int, kind: str, supersedes: int | None = None, marker: str) -> CalculationRun:
    run = CalculationRun(
        reporting_party_id=party_id,
        tax_type="VAT",
        tax_period=date(2026, 8, 1),
        run_kind=kind,
        run_status="SUCCEEDED",
        ruleset_version="TASK13_TEST_V1",
        input_snapshot_sha256=(marker * 64)[:64],
        result_sha256=((marker.upper() if marker.isalpha() else "F") * 64)[:64],
        supersedes_run_id=supersedes,
        created_by="pytest",
        completed_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


def test_postgresql_closed_period_requires_explicit_restatement(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party_id = _new_internal_party(session)
            standard = _succeeded_run(session, party_id=party_id, kind="STANDARD", marker="a")
            state = TaxPeriodState(
                reporting_party_id=party_id,
                tax_type="VAT",
                tax_period=date(2026, 8, 1),
                state="OPEN",
                current_run_id=standard.id,
            )
            session.add(state)
            session.flush()

            state.state = "CLOSED"
            state.closed_run_id = standard.id
            state.closed_by = "pytest"
            state.closed_at = datetime.now(timezone.utc)
            session.flush()
            session.refresh(state)
            assert state.state == "CLOSED"
            assert state.state_version == 2

            nested = session.begin_nested()
            state.state = "OPEN"
            with pytest.raises(DBAPIError):
                session.flush()
            nested.rollback()
            session.refresh(state)

            replacement = _succeeded_run(session, party_id=party_id, kind="STANDARD", marker="b")
            nested = session.begin_nested()
            state.current_run_id = replacement.id
            with pytest.raises(DBAPIError):
                session.flush()
            nested.rollback()
            session.refresh(state)

            bad_restatement = _succeeded_run(session, party_id=party_id, kind="RESTATEMENT", supersedes=replacement.id, marker="c")
            nested = session.begin_nested()
            state.current_run_id = bad_restatement.id
            with pytest.raises(DBAPIError):
                session.flush()
            nested.rollback()
            session.refresh(state)

            restatement = _succeeded_run(session, party_id=party_id, kind="RESTATEMENT", supersedes=standard.id, marker="d")
            state.current_run_id = restatement.id
            session.flush()
            session.refresh(state)
            assert state.state == "CLOSED"
            assert state.closed_run_id == standard.id
            assert state.current_run_id == restatement.id
            assert state.state_version == 3
        finally:
            session.close()
            tx.rollback()


def test_postgresql_terminal_calculation_run_is_immutable(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party_id = _new_internal_party(session)
            run = _succeeded_run(session, party_id=party_id, kind="STANDARD", marker="e")
            nested = session.begin_nested()
            run.note = "attempted silent rewrite"
            with pytest.raises(DBAPIError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()
