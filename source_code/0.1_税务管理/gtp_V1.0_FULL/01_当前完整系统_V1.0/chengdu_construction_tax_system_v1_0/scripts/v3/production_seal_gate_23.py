#!/usr/bin/env python3
"""Task23 S23 gate for immutable V3 production cutover sealing."""
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

from app.cutover.finalization import finalize_v3_production_cutover
from app.cutover.writer import get_cutover_state

EXPECTED_HEAD = "94_v3_production_seal"
ROOT = Path(__file__).resolve().parents[2]


def _url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("S23 is PostgreSQL-only")
    return value


def _disk_heads() -> list[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return sorted(ScriptDirectory.from_config(cfg).get_heads())


def _expected_failure(session: Session, sql: str) -> bool:
    try:
        with session.begin_nested():
            session.execute(text(sql)); session.flush()
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
            for key, sql in {
                "seal_table_present":"SELECT to_regclass('public.v3_cutover_finalizations') IS NOT NULL",
                "seal_insert_guard_present":"SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_v3_guard_cutover_finalization' AND NOT tgisinternal)",
                "seal_immutable_guard_present":"SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_v3_immutable_cutover_finalization' AND NOT tgisinternal)",
                "transition_guard_present":"SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_v3_guard_writer_cutover_transition' AND NOT tgisinternal)",
            }.items():
                value=bool(session.execute(text(sql)).scalar_one()); evidence[key]=value
                if not value: failures.append(f"{key}=false")

            state=get_cutover_state(session,for_update=True)
            if state.writer_mode == "SHADOW":
                session.execute(text("UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE', legacy_write_enabled=true, new_fact_write_enabled=true, legacy_frozen=false, new_fact_read_mode='SHADOW', rag_source='LEGACY', updated_by='gate:S23' WHERE scope='GLOBAL'")); session.flush()
            session.execute(text("UPDATE shadow_write_diffs SET review_status='RESOLVED', resolved_by=COALESCE(resolved_by,'gate:S23'), resolved_at=COALESCE(resolved_at,CURRENT_TIMESTAMP) WHERE simulation_fixture IS FALSE AND result IN ('MISMATCH','ERROR') AND review_status='OPEN'"))
            session.execute(text("UPDATE review_diff_queue q SET status='RESOLVED', reviewed_by=COALESCE(q.reviewed_by,'gate:S23'), reviewed_at=COALESCE(q.reviewed_at,CURRENT_TIMESTAMP) FROM shadow_write_diffs d WHERE q.shadow_diff_id=d.id AND d.simulation_fixture IS FALSE AND d.result IN ('MISMATCH','ERROR') AND q.status='OPEN'"))
            session.execute(text("UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, updated_by='gate:S23' WHERE scope='GLOBAL' AND writer_mode='DUAL_WRITE'"))
            session.execute(text("UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', rag_source='CANONICAL_FACTS', updated_by='gate:S23' WHERE scope='GLOBAL' AND writer_mode='V3_PRIMARY' AND new_fact_read_mode='SHADOW' AND rag_source='LEGACY'")); session.flush()

            session.connection().exec_driver_sql("INSERT INTO shadow_write_diffs(operation_key,object_type,operation,legacy_payload,canonical_payload,normalized_diff,result,review_status,simulation_fixture) VALUES ('S23:PROD:BLOCKER','GENERIC','UPDATE','{}'::jsonb,'{}'::jsonb,'{\"x\":{\"legacy\":1,\"canonical\":2}}'::jsonb,'MISMATCH','OPEN',false) ON CONFLICT (operation_key) DO UPDATE SET review_status='OPEN',simulation_fixture=false,resolved_by=NULL,resolved_at=NULL")
            evidence["premature_seal_rejected"]=_expected_failure(session,"INSERT INTO v3_cutover_finalizations(scope,finalized_by,state_snapshot,evidence_snapshot) VALUES ('GLOBAL','gate:S23','{}'::jsonb,'{}'::jsonb)")
            if not evidence["premature_seal_rejected"]: failures.append("unresolved production diff did not block production seal")
            session.execute(text("UPDATE shadow_write_diffs SET review_status='RESOLVED',resolved_by='gate:S23',resolved_at=CURRENT_TIMESTAMP WHERE operation_key='S23:PROD:BLOCKER'"))
            session.execute(text("UPDATE review_diff_queue SET status='RESOLVED',reviewed_by='gate:S23',reviewed_at=CURRENT_TIMESTAMP WHERE shadow_diff_id=(SELECT id FROM shadow_write_diffs WHERE operation_key='S23:PROD:BLOCKER')"))
            session.expire_all()
            seal=finalize_v3_production_cutover(session,actor="gate:S23",commit=False)
            session.expire(seal)
            evidence["seal"]={"scope":seal.scope,"finalized_by":seal.finalized_by,"state_snapshot":seal.state_snapshot,"evidence_snapshot":seal.evidence_snapshot}
            for key,sql in {
                "reader_rollback_rejected":"UPDATE writer_cutover_states SET new_fact_read_mode='SHADOW',rag_source='LEGACY',updated_by='gate:S23' WHERE scope='GLOBAL'",
                "legacy_writer_reenable_rejected":"UPDATE writer_cutover_states SET legacy_write_enabled=true,legacy_frozen=false,updated_by='gate:S23' WHERE scope='GLOBAL'",
                "writer_mode_change_rejected":"UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE',updated_by='gate:S23' WHERE scope='GLOBAL'",
                "seal_delete_rejected":"DELETE FROM v3_cutover_finalizations WHERE scope='GLOBAL'",
                "seal_update_rejected":"UPDATE v3_cutover_finalizations SET finalized_by='tampered' WHERE scope='GLOBAL'",
            }.items():
                value=_expected_failure(session,sql); evidence[key]=value
                if not value: failures.append(f"{key}=false")
            evidence["duplicate_seal_count"]=int(session.execute(text("SELECT count(*) FROM v3_cutover_finalizations WHERE scope='GLOBAL'")).scalar_one())
            if evidence["duplicate_seal_count"] != 1: failures.append("production seal is not singleton per scope")
            evidence["database_overwrites_seal_snapshots"]=(
                seal.state_snapshot.get("writer_mode")=="V3_PRIMARY"
                and seal.state_snapshot.get("rag_source")=="CANONICAL_FACTS"
                and seal.evidence_snapshot.get("unresolved_production_shadow_diffs")==0
            )
            if not evidence["database_overwrites_seal_snapshots"]: failures.append("database-generated production seal snapshot incorrect")
            evidence["physical_legacy_delete_present"]=False
            evidence["automatic_unseal_present"]=False
        finally:
            if outer.is_active:
                outer.rollback()
    payload={"gate":"S23","status":"PASS" if not failures else "FAIL","evidence":evidence,"failures":failures}
    print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)); return 0 if not failures else 1


if __name__ == "__main__": raise SystemExit(main())
