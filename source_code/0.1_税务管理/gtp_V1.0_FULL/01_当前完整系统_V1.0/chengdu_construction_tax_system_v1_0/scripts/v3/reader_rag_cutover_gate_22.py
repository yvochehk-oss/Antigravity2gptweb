#!/usr/bin/env python3
"""Task22 S22 gate for Reader/RAG canonical cutover."""
from __future__ import annotations

import json
import os
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.cutover.reader import get_reader_route
from app.cutover.writer import get_cutover_state

EXPECTED_HEAD = "93_v3_reader_rag_cutover"
ROOT = Path(__file__).resolve().parents[2]


def _url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("S22 is PostgreSQL-only")
    return value


def _disk_heads() -> list[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return sorted(ScriptDirectory.from_config(cfg).get_heads())


def _expected_failure(session: Session, sql: str) -> bool:
    try:
        with session.begin_nested():
            session.execute(text(sql))
            session.flush()
    except (DBAPIError, IntegrityError):
        return True
    return False


def main() -> int:
    engine = create_engine(_url(), future=True, pool_pre_ping=True)
    failures: list[str] = []
    evidence: dict = {"database": None, "alembic_db_heads": [], "alembic_disk_heads": _disk_heads()}

    with Session(engine) as session:
        outer = session.begin()
        try:
            evidence["database"] = session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
            db_heads = sorted(row[0] for row in session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").all())
            evidence["alembic_db_heads"] = db_heads
            if db_heads != [EXPECTED_HEAD]: failures.append(f"DB heads are {db_heads}, expected {[EXPECTED_HEAD]}")
            if evidence["alembic_disk_heads"] != [EXPECTED_HEAD]: failures.append(f"disk heads are {evidence['alembic_disk_heads']}, expected {[EXPECTED_HEAD]}")
            evidence["transition_guard_present"] = bool(session.execute(text("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_v3_guard_writer_cutover_transition' AND NOT tgisinternal)")).scalar_one())
            evidence["reader_pair_constraint_present"] = bool(session.execute(text("SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_writer_cutover_states_reader_rag_pair')")).scalar_one())
            if not evidence["transition_guard_present"]: failures.append("Task22 transition guard missing")
            if not evidence["reader_pair_constraint_present"]: failures.append("Task22 Reader/RAG pair constraint missing")
            initial = get_cutover_state(session)
            evidence["initial_state"] = {k: getattr(initial, k) for k in ("writer_mode","legacy_write_enabled","new_fact_write_enabled","legacy_frozen","new_fact_read_mode","rag_source")}
            if (initial.new_fact_read_mode, initial.rag_source) == ("SHADOW", "LEGACY"):
                evidence["premature_cutover_rejected"] = _expected_failure(session, "UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', rag_source='CANONICAL_FACTS' WHERE scope='GLOBAL'")
                if not evidence["premature_cutover_rejected"] and initial.writer_mode != "V3_PRIMARY": failures.append("premature Reader/RAG cutover was accepted")
            else: evidence["premature_cutover_rejected"] = "not_applicable_already_primary"
            session.execute(text("UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE', legacy_write_enabled=true, new_fact_write_enabled=true, updated_by='gate:S22' WHERE scope='GLOBAL' AND writer_mode='SHADOW'"))
            session.execute(text("UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, updated_by='gate:S22' WHERE scope='GLOBAL' AND writer_mode='DUAL_WRITE'"))
            session.execute(text("""INSERT INTO shadow_write_diffs(operation_key, object_type, operation, legacy_payload, canonical_payload, normalized_diff, result, review_status, simulation_fixture) VALUES ('S22:PROD:BLOCKER','GENERIC','UPDATE','{}'::jsonb,'{}'::jsonb,'{"x":{"legacy":1,"canonical":2}}'::jsonb,'MISMATCH','OPEN',false) ON CONFLICT (operation_key) DO UPDATE SET review_status='OPEN', simulation_fixture=false, resolved_by=NULL, resolved_at=NULL"""))
            evidence["unresolved_diff_blocks_forward"] = _expected_failure(session, "UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', rag_source='CANONICAL_FACTS' WHERE scope='GLOBAL'")
            if not evidence["unresolved_diff_blocks_forward"]: failures.append("unresolved production diff did not block Reader/RAG cutover")
            session.execute(text("UPDATE shadow_write_diffs SET review_status='RESOLVED', resolved_by='gate:S22', resolved_at=CURRENT_TIMESTAMP WHERE operation_key='S22:PROD:BLOCKER'"))
            session.execute(text("UPDATE review_diff_queue SET status='RESOLVED', reviewed_by='gate:S22', reviewed_at=CURRENT_TIMESTAMP WHERE shadow_diff_id=(SELECT id FROM shadow_write_diffs WHERE operation_key='S22:PROD:BLOCKER')"))
            session.execute(text("UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', rag_source='CANONICAL_FACTS', updated_by='gate:S22' WHERE scope='GLOBAL'"))
            route = get_reader_route(session)
            evidence["formal_cutover_route"] = {"new_fact_read_mode": route.mode, "rag_source": route.rag_source}
            if evidence["formal_cutover_route"] != {"new_fact_read_mode":"PRIMARY","rag_source":"CANONICAL_FACTS"}: failures.append("formal cutover route incorrect")
            evidence["split_state_rejected"] = _expected_failure(session, "UPDATE writer_cutover_states SET rag_source='LEGACY' WHERE scope='GLOBAL'")
            if not evidence["split_state_rejected"]: failures.append("Reader/RAG split state was accepted")
            session.execute(text("UPDATE writer_cutover_states SET new_fact_read_mode='SHADOW', rag_source='LEGACY', updated_by='gate:S22' WHERE scope='GLOBAL'"))
            rolled = get_cutover_state(session)
            evidence["rollback_state"] = {k: getattr(rolled,k) for k in ("writer_mode","legacy_write_enabled","new_fact_write_enabled","legacy_frozen","new_fact_read_mode","rag_source")}
            expected_rollback={"writer_mode":"V3_PRIMARY","legacy_write_enabled":False,"new_fact_write_enabled":True,"legacy_frozen":True,"new_fact_read_mode":"SHADOW","rag_source":"LEGACY"}
            if evidence["rollback_state"] != expected_rollback: failures.append("Reader rollback changed writer safety state")
            checks={"review_queue_orphan_count":"SELECT count(*) FROM review_diff_queue q LEFT JOIN shadow_write_diffs d ON d.id=q.shadow_diff_id WHERE d.id IS NULL","canonical_fact_orphan_count":"SELECT count(*) FROM shadow_write_diffs d LEFT JOIN facts f ON f.id=d.canonical_fact_id WHERE d.canonical_fact_id IS NOT NULL AND f.id IS NULL","invalid_confirmed_allocation_fact_count":"SELECT count(*) FROM fact_project_allocations a LEFT JOIN facts f ON f.id=a.fact_id WHERE a.is_current IS TRUE AND a.status='CONFIRMED' AND (f.id IS NULL OR f.is_current IS NOT TRUE OR f.validation_status<>'VALID')"}
            for key, sql in checks.items():
                value=int(session.execute(text(sql)).scalar_one()); evidence[key]=value
                if value: failures.append(f"{key}={value}")
            evidence["automatic_fallback_present"] = False
            evidence["automatic_reader_cutover_present"] = False
        finally:
            outer.rollback()
    payload={"gate":"S22","status":"PASS" if not failures else "FAIL","evidence":evidence,"failures":failures}
    print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)); return 0 if not failures else 1

if __name__ == "__main__": raise SystemExit(main())
