#!/usr/bin/env python3
"""Read-only Gate S13 verifier for Calculation Runs / Tax Period States."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_HEAD = "83_v3_calculation_runs_period_states"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S13 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    config = Config(str(ROOT / "alembic.ini"))
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        config.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(config).get_heads())


def run() -> dict[str, Any]:
    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    failures: list[str] = []

    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            inspector = inspect(conn)
            db_heads = {str(row[0]) for row in conn.execute(text("SELECT version_num FROM alembic_version_tax")) if row[0]}
            disk_heads = _disk_heads()
            if db_heads != {EXPECTED_HEAD}:
                failures.append(f"formal DB head must be {EXPECTED_HEAD}: {sorted(db_heads)}")
            if disk_heads != {EXPECTED_HEAD}:
                failures.append(f"disk head must be {EXPECTED_HEAD}: {sorted(disk_heads)}")

            tables = set(inspector.get_table_names(schema="public"))
            required = {"calculation_runs", "tax_period_states"}
            missing = sorted(required - tables)
            if missing:
                failures.append(f"missing Task13 tables: {missing}")

            run_columns = {row["name"] for row in inspector.get_columns("calculation_runs", schema="public")} if "calculation_runs" in tables else set()
            state_columns = {row["name"] for row in inspector.get_columns("tax_period_states", schema="public")} if "tax_period_states" in tables else set()
            prohibited = {"project_id", "cashflow_id", "bank_transaction_id", "invoice_fact_id", "recognized_revenue", "cost_amount"}
            run_prohibited = sorted(run_columns & prohibited)
            state_prohibited = sorted(state_columns & prohibited)
            if run_prohibited:
                failures.append(f"calculation_runs mixed/prohibited columns: {run_prohibited}")
            if state_prohibited:
                failures.append(f"tax_period_states mixed/prohibited columns: {state_prohibited}")

            trigger_names = {
                str(row[0])
                for row in conn.execute(
                    text("SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema='public' AND event_object_table IN ('calculation_runs','tax_period_states')")
                )
            }
            required_triggers = {"trg_v3_guard_calculation_run_update", "trg_v3_guard_tax_period_state"}
            missing_triggers = sorted(required_triggers - trigger_names)
            if missing_triggers:
                failures.append(f"missing Task13 protection triggers: {missing_triggers}")

            runs = {}
            states = []
            if required <= tables:
                for row in conn.execute(text("SELECT id, reporting_party_id, tax_type, tax_period, run_kind, run_status, supersedes_run_id, completed_at FROM calculation_runs")):
                    runs[int(row.id)] = dict(row._mapping)
                states = [dict(row._mapping) for row in conn.execute(text("SELECT id, reporting_party_id, tax_type, tax_period, state, current_run_id, closed_run_id, closed_by, closed_at, state_version FROM tax_period_states"))]

            invalid_chain_ids: list[int] = []
            invalid_open_ids: list[int] = []
            invalid_scope_ids: list[int] = []
            for state in states:
                scope = (state["reporting_party_id"], state["tax_type"], state["tax_period"])
                current_id = state["current_run_id"]
                current = runs.get(int(current_id)) if current_id is not None else None
                if current is not None:
                    run_scope = (current["reporting_party_id"], current["tax_type"], current["tax_period"])
                    if run_scope != scope or current["run_status"] != "SUCCEEDED":
                        invalid_scope_ids.append(int(state["id"]))

                if state["state"] == "OPEN":
                    if state["closed_run_id"] is not None or state["closed_by"] is not None or state["closed_at"] is not None:
                        invalid_open_ids.append(int(state["id"]))
                    if current is not None and current["run_kind"] != "STANDARD":
                        invalid_open_ids.append(int(state["id"]))
                    continue

                if state["state"] != "CLOSED" or current is None or state["closed_run_id"] is None:
                    invalid_chain_ids.append(int(state["id"]))
                    continue
                closed_id = int(state["closed_run_id"])
                closed = runs.get(closed_id)
                if closed is None or closed["run_kind"] != "STANDARD" or closed["run_status"] != "SUCCEEDED":
                    invalid_chain_ids.append(int(state["id"]))
                    continue
                if (closed["reporting_party_id"], closed["tax_type"], closed["tax_period"]) != scope:
                    invalid_chain_ids.append(int(state["id"]))
                    continue

                cursor = int(current_id)
                seen: set[int] = set()
                ok = True
                while cursor != closed_id:
                    if cursor in seen:
                        ok = False
                        break
                    seen.add(cursor)
                    run_row = runs.get(cursor)
                    if run_row is None or run_row["run_kind"] != "RESTATEMENT" or run_row["run_status"] != "SUCCEEDED" or run_row["supersedes_run_id"] is None:
                        ok = False
                        break
                    if (run_row["reporting_party_id"], run_row["tax_type"], run_row["tax_period"]) != scope:
                        ok = False
                        break
                    cursor = int(run_row["supersedes_run_id"])
                if not ok:
                    invalid_chain_ids.append(int(state["id"]))

            invalid_run_period_count = 0
            invalid_terminal_count = 0
            invalid_restatement_count = 0
            if required <= tables:
                invalid_run_period_count = int(conn.execute(text("SELECT count(*) FROM calculation_runs WHERE EXTRACT(DAY FROM tax_period)<>1")).scalar_one())
                invalid_terminal_count = int(conn.execute(text("SELECT count(*) FROM calculation_runs WHERE run_status<>'DRAFT' AND completed_at IS NULL")).scalar_one())
                invalid_restatement_count = int(conn.execute(text("SELECT count(*) FROM calculation_runs WHERE (run_kind='STANDARD' AND supersedes_run_id IS NOT NULL) OR (run_kind='RESTATEMENT' AND supersedes_run_id IS NULL)")).scalar_one())
            if invalid_run_period_count:
                failures.append(f"calculation runs with non-month-start period: {invalid_run_period_count}")
            if invalid_terminal_count:
                failures.append(f"terminal calculation runs missing completed_at: {invalid_terminal_count}")
            if invalid_restatement_count:
                failures.append(f"calculation runs with invalid restatement link: {invalid_restatement_count}")
            if invalid_scope_ids:
                failures.append(f"period states with invalid current-run scope/status: {sorted(set(invalid_scope_ids))}")
            if invalid_open_ids:
                failures.append(f"invalid OPEN period states: {sorted(set(invalid_open_ids))}")
            if invalid_chain_ids:
                failures.append(f"invalid CLOSED/restatement chains: {sorted(set(invalid_chain_ids))}")

            evidence = {
                "database": make_url(database_url).database,
                "alembic_db_heads": sorted(db_heads),
                "alembic_disk_heads": sorted(disk_heads),
                "run_prohibited_columns": run_prohibited,
                "state_prohibited_columns": state_prohibited,
                "trigger_names": sorted(trigger_names),
                "missing_protection_triggers": missing_triggers,
                "calculation_run_count": len(runs),
                "tax_period_state_count": len(states),
                "closed_period_count": sum(1 for item in states if item["state"] == "CLOSED"),
                "invalid_run_period_count": invalid_run_period_count,
                "invalid_terminal_run_count": invalid_terminal_count,
                "invalid_restatement_link_count": invalid_restatement_count,
                "invalid_current_scope_state_ids": sorted(set(invalid_scope_ids)),
                "invalid_open_state_ids": sorted(set(invalid_open_ids)),
                "invalid_closed_chain_state_ids": sorted(set(invalid_chain_ids)),
            }
        finally:
            conn.exec_driver_sql("ROLLBACK")
    engine.dispose()
    return {"status": "PASS" if not failures else "FAIL", "failures": failures, "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    result = run()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
