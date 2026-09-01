#!/usr/bin/env python3
"""Task21 S21 gate for Phase-1 writer shadow cutover."""
from __future__ import annotations

import json
import os
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.cutover.writer import ShadowProjection, get_cutover_state, record_shadow_projection

EXPECTED_HEAD = "92_v3_writer_shadow_cutover"
FIXTURE_KEYS = ("S21:FIXTURE:MATCH", "S21:FIXTURE:MISMATCH", "S21:FIXTURE:ERROR")
ROOT = Path(__file__).resolve().parents[2]


def _url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("S21 is PostgreSQL-only")
    return value


def _disk_heads() -> list[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return sorted(ScriptDirectory.from_config(cfg).get_heads())


def _seed_fixtures(session: Session) -> dict:
    session.execute(text("DELETE FROM shadow_write_diffs WHERE operation_key = ANY(:keys)"), {"keys": list(FIXTURE_KEYS)})
    session.commit()
    match = record_shadow_projection(session, operation_key=FIXTURE_KEYS[0], object_type="GENERIC", operation="INSERT", legacy_payload={"amount": "100.00", "currency": "CNY"}, simulation_fixture=True, shadow_writer=lambda _db: ShadowProjection(payload={"amount": "100.00", "currency": "CNY"}))
    mismatch = record_shadow_projection(session, operation_key=FIXTURE_KEYS[1], object_type="GENERIC", operation="UPDATE", legacy_payload={"amount": "100.00"}, simulation_fixture=True, shadow_writer=lambda _db: ShadowProjection(payload={"amount": "99.00"}))
    def fail(_db):
        raise RuntimeError("synthetic Task21 shadow failure")
    error = record_shadow_projection(session, operation_key=FIXTURE_KEYS[2], object_type="GENERIC", operation="INSERT", legacy_payload={"amount": "1.00"}, simulation_fixture=True, shadow_writer=fail)
    review_count = int(session.execute(text("SELECT count(*) FROM review_diff_queue q JOIN shadow_write_diffs d ON d.id=q.shadow_diff_id WHERE d.operation_key = ANY(:keys) AND q.status='OPEN'"), {"keys": list(FIXTURE_KEYS)}).scalar_one())
    return {"match": match.result, "mismatch": mismatch.result, "error": error.result, "open_review_count": review_count}


def main() -> int:
    engine = create_engine(_url(), future=True, pool_pre_ping=True)
    failures: list[str] = []
    disk_heads = _disk_heads()
    evidence: dict = {"database": None, "alembic_db_heads": [], "alembic_disk_heads": disk_heads}
    with Session(engine) as session:
        evidence["database"] = session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
        db_heads = sorted(row[0] for row in session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").all())
        evidence["alembic_db_heads"] = db_heads
        if db_heads != [EXPECTED_HEAD]:
            failures.append(f"DB heads are {db_heads}, expected {[EXPECTED_HEAD]}")
        if disk_heads != [EXPECTED_HEAD]:
            failures.append(f"disk heads are {disk_heads}, expected {[EXPECTED_HEAD]}")

        state = get_cutover_state(session)
        state_payload = {"writer_mode": state.writer_mode, "legacy_write_enabled": state.legacy_write_enabled, "new_fact_write_enabled": state.new_fact_write_enabled, "rag_source": state.rag_source, "new_fact_read_mode": state.new_fact_read_mode, "legacy_frozen": state.legacy_frozen}
        evidence["cutover_state"] = state_payload
        expected_state = {"writer_mode": "SHADOW", "legacy_write_enabled": True, "new_fact_write_enabled": False, "rag_source": "LEGACY", "new_fact_read_mode": "SHADOW", "legacy_frozen": False}
        if state_payload != expected_state:
            failures.append(f"Task21 must remain in Phase-1 SHADOW state: {state_payload}")

        evidence["review_trigger_present"] = bool(session.execute(text("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_v3_enqueue_shadow_diff_review' AND NOT tgisinternal)")).scalar_one())
        evidence["transition_guard_present"] = bool(session.execute(text("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_v3_guard_writer_cutover_transition' AND NOT tgisinternal)")).scalar_one())
        if not evidence["review_trigger_present"]:
            failures.append("shadow diff review trigger missing")
        if not evidence["transition_guard_present"]:
            failures.append("writer cutover lifecycle trigger missing")

        checks = {
            "unresolved_production_diff_count": "SELECT count(*) FROM shadow_write_diffs WHERE simulation_fixture IS FALSE AND result IN ('MISMATCH','ERROR') AND review_status='OPEN'",
            "reviewable_diff_without_queue_count": "SELECT count(*) FROM shadow_write_diffs d WHERE d.result IN ('MISMATCH','ERROR') AND NOT EXISTS (SELECT 1 FROM review_diff_queue q WHERE q.shadow_diff_id=d.id)",
            "review_queue_orphan_count": "SELECT count(*) FROM review_diff_queue q LEFT JOIN shadow_write_diffs d ON d.id=q.shadow_diff_id WHERE d.id IS NULL",
            "canonical_fact_orphan_count": "SELECT count(*) FROM shadow_write_diffs d LEFT JOIN facts f ON f.id=d.canonical_fact_id WHERE d.canonical_fact_id IS NOT NULL AND f.id IS NULL",
        }
        for key, sql in checks.items():
            value = int(session.execute(text(sql)).scalar_one())
            evidence[key] = value
            if value:
                failures.append(f"{key}={value}")

        fixture = _seed_fixtures(session)
        evidence["fixture_shadow_write"] = fixture
        if fixture != {"match": "MATCH", "mismatch": "MISMATCH", "error": "ERROR", "open_review_count": 2}:
            failures.append(f"shadow fixture mismatch: {fixture}")

        state = get_cutover_state(session)
        evidence["automatic_dual_write_detected"] = state.writer_mode != "SHADOW"
        evidence["rag_cutover_detected"] = state.rag_source != "LEGACY" or state.new_fact_read_mode != "SHADOW"
        evidence["legacy_freeze_detected"] = bool(state.legacy_frozen or not state.legacy_write_enabled)
        if evidence["automatic_dual_write_detected"]:
            failures.append("automatic DUAL_WRITE/V3_PRIMARY transition detected")
        if evidence["rag_cutover_detected"]:
            failures.append("RAG/reader cutover is outside Task21")
        if evidence["legacy_freeze_detected"]:
            failures.append("legacy writer must remain active during S21 shadow validation")

    payload = {"gate": "S21", "status": "PASS" if not failures else "FAIL", "evidence": evidence, "failures": failures}
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
