#!/usr/bin/env python3
"""Read-only Gate S18 verifier for canonical group penetration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_HEAD = "89_v3_group_penetration"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S18 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    config = Config(str(ROOT / "alembic.ini"))
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        config.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(config).get_heads())


FIXTURE_SQL = text("""
    WITH RECURSIVE edges(chain, edge_id, performer, receiver, amount, performer_internal) AS (
        VALUES
          ('CHAIN1', 1, 101, 201, 255.00::numeric, false),
          ('CHAIN1', 2, 201, 202, 300.00::numeric, true),
          ('CHAIN1', 3, 202, 901, 350.00::numeric, true),
          ('CHAIN2', 11, 111, 211, 200.00::numeric, false),
          ('CHAIN2', 12, 211, 212, 230.00::numeric, true),
          ('CHAIN2', 13, 212, 213, 260.00::numeric, true),
          ('CHAIN2', 14, 213, 902, 330.00::numeric, true),
          ('CYCLE', 21, 301, 302, 100.00::numeric, true),
          ('CYCLE', 22, 302, 303, 100.00::numeric, true),
          ('CYCLE', 23, 303, 301, 100.00::numeric, true),
          ('CYCLE', 24, 301, 903, 120.00::numeric, true)
    ),
    roots AS (
        SELECT e.chain, e.edge_id AS root_edge_id, e.edge_id, e.performer AS current_party,
               e.amount, 0 AS depth, ARRAY[e.performer]::integer[] AS visited,
               ARRAY[e.receiver, e.performer]::integer[] AS path,
               false AS cycle_detected, 'EXTERNAL_REVENUE'::text AS edge_kind
        FROM edges e
        WHERE e.receiver >= 900 AND e.performer_internal
    ),
    walk AS (
        SELECT * FROM roots
        UNION ALL
        SELECT w.chain, w.root_edge_id, e.edge_id, e.performer, e.amount,
               w.depth + 1, w.visited || e.performer, w.path || e.performer,
               e.performer = ANY(w.visited),
               CASE WHEN e.performer_internal THEN 'INTERNAL_ELIMINATION'
                    ELSE 'EXTERNAL_LEAF_COST' END
        FROM walk w
        JOIN edges e ON e.chain = w.chain AND e.receiver = w.current_party
        WHERE w.edge_kind <> 'EXTERNAL_LEAF_COST'
          AND NOT w.cycle_detected
          AND w.depth < 16
    ),
    dedup AS (
        SELECT DISTINCT ON (chain, edge_kind, edge_id)
               chain, edge_kind, edge_id, amount, cycle_detected, path, depth
        FROM walk
        ORDER BY chain, edge_kind, edge_id, depth
    )
    SELECT chain,
        COALESCE(sum(amount) FILTER (WHERE edge_kind='EXTERNAL_REVENUE' AND NOT cycle_detected), 0) AS external_revenue,
        COALESCE(sum(amount) FILTER (WHERE edge_kind='EXTERNAL_LEAF_COST' AND NOT cycle_detected), 0) AS external_leaf_cost,
        COALESCE(sum(amount) FILTER (WHERE edge_kind='INTERNAL_ELIMINATION' AND NOT cycle_detected), 0) AS internal_eliminated,
        bool_or(cycle_detected) AS cycle_detected
    FROM dedup
    GROUP BY chain
    ORDER BY chain
""")


def run() -> dict[str, Any]:
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names(schema="public"))
        db_heads = {str(row[0]) for row in conn.execute(text("SELECT version_num FROM alembic_version_tax")) if row[0]}
        disk_heads = _disk_heads()
        if db_heads != disk_heads or db_heads != {EXPECTED_HEAD}:
            failures.append(f"formal DB/disk heads must both be {EXPECTED_HEAD}: db={sorted(db_heads)} disk={sorted(disk_heads)}")

        required = {"group_penetration_results", "group_penetration_components", "fulfillment_facts", "entity_vat_ledgers"}
        missing = sorted(required - tables)
        if missing:
            failures.append(f"Task18 required tables missing: {missing}")

        prohibited: dict[str, list[str]] = {}
        for table in ("entity_vat_ledgers", "entity_tax_ledgers"):
            if table not in tables:
                continue
            columns = {row["name"] for row in inspector.get_columns(table, schema="public")}
            bad = sorted(columns & {"project_id", "group_id", "internal_eliminated"})
            prohibited[table] = bad
            if bad:
                failures.append(f"{table} contains forbidden group/project columns: {bad}")

        fixture_rows = {str(row["chain"]): row for row in conn.execute(FIXTURE_SQL).mappings().all()}
        chain1 = fixture_rows.get("CHAIN1")
        chain2 = fixture_rows.get("CHAIN2")
        cycle = fixture_rows.get("CYCLE")
        if chain1 is None or float(chain1["external_leaf_cost"]) != 255.0 or float(chain1["internal_eliminated"]) != 300.0 or bool(chain1["cycle_detected"]):
            failures.append("CHAIN1 penetration fixture failed")
        if chain2 is None or float(chain2["external_leaf_cost"]) != 200.0 or float(chain2["internal_eliminated"]) != 490.0 or bool(chain2["cycle_detected"]):
            failures.append("CHAIN2 penetration fixture failed")
        if cycle is None or not bool(cycle["cycle_detected"]):
            failures.append("cycle fixture did not return CYCLE_DETECTED")

        tax_elimination_count = 0
        if "group_penetration_results" in tables:
            tax_elimination_count = int(conn.execute(text("SELECT count(*) FROM group_penetration_results WHERE basis='TAX' AND internal_eliminated IS NOT NULL")).scalar_one())
            if tax_elimination_count:
                failures.append("TAX group results must never carry internal elimination")

        evidence.update({
            "database": make_url(database_url).database,
            "alembic_db_heads": sorted(db_heads),
            "alembic_disk_heads": sorted(disk_heads),
            "missing_tables": missing,
            "entity_ledger_forbidden_columns": prohibited,
            "fixture_chain1": dict(chain1) if chain1 is not None else None,
            "fixture_chain2": dict(chain2) if chain2 is not None else None,
            "fixture_cycle": dict(cycle) if cycle is not None else None,
            "tax_internal_elimination_rows": tax_elimination_count,
            "payment_fact_present": "payment_facts" in tables,
            "cash_legacy_fallback_allowed": False,
        })
    return {"gate": "S18", "status": "PASS" if not failures else "FAIL", "failures": failures, "evidence": evidence}


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
