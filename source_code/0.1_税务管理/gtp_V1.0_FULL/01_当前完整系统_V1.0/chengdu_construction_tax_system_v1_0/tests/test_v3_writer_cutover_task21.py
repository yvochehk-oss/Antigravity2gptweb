"""PostgreSQL integration tests for Task21 writer shadow cutover."""
from __future__ import annotations

from decimal import Decimal
import uuid

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.cutover.writer import CutoverError, ShadowProjection, get_cutover_state, record_shadow_projection, transition_to_dual_write
from app.models import CashFlow, Project
from app.v3_cutover_models import ReviewDiffQueue, ShadowWriteDiff


def _key(prefix: str) -> str:
    return f"task21:{prefix}:{uuid.uuid4()}"


def test_task21_schema_and_initial_state(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    assert {"writer_cutover_states", "shadow_write_diffs", "review_diff_queue"} <= set(inspect(engine).get_table_names())
    with Session(engine) as session:
        state = get_cutover_state(session)
        assert (state.writer_mode, state.legacy_write_enabled, state.new_fact_write_enabled) == ("SHADOW", True, False)
        assert (state.rag_source, state.new_fact_read_mode, state.legacy_frozen) == ("LEGACY", "SHADOW", False)


def test_shadow_match_auto_resolves_without_review(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        payload = {"amount": Decimal("100.00"), "currency": "CNY"}
        out = record_shadow_projection(session, operation_key=_key("match"), object_type="PAYMENT", operation="INSERT", legacy_payload=payload, simulation_fixture=True, shadow_writer=lambda _db: ShadowProjection(payload=payload))
        assert out.result == "MATCH"
        row = session.get(ShadowWriteDiff, out.diff_id)
        assert row.review_status == "RESOLVED"
        assert session.scalar(select(ReviewDiffQueue).where(ReviewDiffQueue.shadow_diff_id == out.diff_id)) is None


def test_shadow_mismatch_enters_review_queue(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        out = record_shadow_projection(session, operation_key=_key("mismatch"), object_type="INVOICE", operation="UPDATE", legacy_payload={"amount": "100.00"}, simulation_fixture=True, shadow_writer=lambda _db: ShadowProjection(payload={"amount": "99.00"}))
        assert out.result == "MISMATCH"
        queue = session.scalar(select(ReviewDiffQueue).where(ReviewDiffQueue.shadow_diff_id == out.diff_id))
        assert queue is not None and queue.status == "OPEN"


def test_shadow_failure_does_not_rollback_committed_legacy_write(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        token = uuid.uuid4().hex[:10]
        project = Project(code=f"T21-{token}", name=f"Task21 {token}", city="TEST", contract_total=Decimal("1.00"), tax_method="general")
        session.add(project); session.flush()
        legacy = CashFlow(project_id=project.id, entity_code="A08", counterparty_code="EXT-T21", direction="out", amount=Decimal("1.00"), period="2099-01", transaction_date="2099-01-01", note="task21")
        session.add(legacy); session.commit(); legacy_id = legacy.id
        def fail_shadow(_db):
            raise RuntimeError("synthetic shadow failure")
        out = record_shadow_projection(session, operation_key=_key("error"), object_type="PAYMENT", operation="INSERT", legacy_object_id=legacy_id, legacy_payload={"amount": "1.00", "direction": "out"}, simulation_fixture=True, shadow_writer=fail_shadow)
        assert out.result == "ERROR"
        assert session.get(CashFlow, legacy_id) is not None
        row = session.get(ShadowWriteDiff, out.diff_id)
        assert row.error_code == "V3_SHADOW_WRITE_FAILED"
        assert session.scalar(select(ReviewDiffQueue).where(ReviewDiffQueue.shadow_diff_id == out.diff_id)) is not None


def test_shadow_operation_key_is_idempotent(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True); calls = {"count": 0}
    with Session(engine) as session:
        key = _key("idem")
        def writer(_db):
            calls["count"] += 1
            return ShadowProjection(payload={"x": 1})
        first = record_shadow_projection(session, operation_key=key, object_type="GENERIC", operation="INSERT", legacy_payload={"x": 1}, simulation_fixture=True, shadow_writer=writer)
        second = record_shadow_projection(session, operation_key=key, object_type="GENERIC", operation="INSERT", legacy_payload={"x": 1}, simulation_fixture=True, shadow_writer=writer)
        assert first.diff_id == second.diff_id and calls["count"] == 1


def test_unresolved_production_diff_blocks_dual_write(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        tx = session.begin()
        session.add(ShadowWriteDiff(operation_key=_key("production-blocker"), object_type="GENERIC", operation="INSERT", legacy_payload={"x": 1}, canonical_payload={"x": 2}, normalized_diff={"x": {"legacy": 1, "canonical": 2}}, result="MISMATCH", review_status="OPEN", simulation_fixture=False))
        session.flush()
        with pytest.raises(CutoverError, match="blocked"):
            transition_to_dual_write(session, actor="pytest")
        tx.rollback()


def test_shadow_state_rejects_rag_cutover(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        tx = session.begin(); state = get_cutover_state(session); state.rag_source = "CANONICAL_FACTS"
        with pytest.raises(DBAPIError):
            session.flush()
        tx.rollback()


def test_database_rejects_shadow_to_v3_primary_jump(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    with Session(engine) as session:
        tx = session.begin()
        with pytest.raises(DBAPIError):
            session.execute(text("UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, updated_by='bypass' WHERE scope='GLOBAL'"))
            session.flush()
        tx.rollback()
