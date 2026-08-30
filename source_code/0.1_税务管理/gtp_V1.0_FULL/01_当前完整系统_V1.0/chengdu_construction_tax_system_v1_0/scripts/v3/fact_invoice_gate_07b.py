#!/usr/bin/env python3
"""Read-only PostgreSQL verifier for Task 07b invoice Fact relationships."""
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
REQUIRED_INVOICE_COLUMNS = {"invoice_medium", "invoice_category", "invoice_status"}
FORBIDDEN_INVOICE_COLUMNS = {"red_blue_flag", "original_invoice_id"}


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task 07b verifier is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    script_location = Path(cfg.get_main_option("script_location"))
    if not script_location.is_absolute():
        cfg.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def run() -> dict[str, Any]:
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    failures: list[str] = []
    evidence: dict[str, Any] = {}

    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            inspector = inspect(conn)
            tables = set(inspector.get_table_names(schema="public"))
            if "fact_relationships" not in tables:
                failures.append("missing Task 07b table: fact_relationships")
            if "invoice_facts" not in tables:
                failures.append("missing invoice_facts")
            else:
                columns = {
                    row["name"] for row in inspector.get_columns("invoice_facts", schema="public")
                }
                missing_columns = sorted(REQUIRED_INVOICE_COLUMNS - columns)
                forbidden_columns = sorted(FORBIDDEN_INVOICE_COLUMNS & columns)
                if missing_columns:
                    failures.append(f"invoice_facts missing Task 07b columns: {missing_columns}")
                if forbidden_columns:
                    failures.append(f"invoice_facts contains duplicate-truth columns: {forbidden_columns}")

            actual_heads = {
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
                if row[0]
            }
            expected_heads = _disk_heads()
            evidence["alembic_db_heads"] = sorted(actual_heads)
            evidence["alembic_disk_heads"] = sorted(expected_heads)
            if actual_heads != expected_heads:
                failures.append(
                    f"Tax Alembic head mismatch: db={sorted(actual_heads)} disk={sorted(expected_heads)}"
                )

            if "fact_relationships" in tables and "invoice_facts" in tables:
                checks = {
                    "relationship_count": "SELECT count(*) FROM fact_relationships",
                    "red_invoice_count": "SELECT count(*) FROM invoice_facts WHERE invoice_status='RED'",
                    "voided_invoice_count": "SELECT count(*) FROM invoice_facts WHERE invoice_status='VOIDED'",
                    "positive_red_amount_count": """
                        SELECT count(*) FROM invoice_facts
                        WHERE invoice_status='RED'
                          AND (coalesce(net_amount,0)>0 OR coalesce(vat_amount,0)>0 OR coalesce(gross_amount,0)>0)
                    """,
                    "self_relationship_count": """
                        SELECT count(*) FROM fact_relationships WHERE source_fact_id=target_fact_id
                    """,
                    "orphan_relationship_count": """
                        SELECT count(*)
                        FROM fact_relationships r
                        LEFT JOIN facts s ON s.id=r.source_fact_id
                        LEFT JOIN facts t ON t.id=r.target_fact_id
                        WHERE s.id IS NULL OR t.id IS NULL
                    """,
                    "invalid_relationship_type_count": """
                        SELECT count(*) FROM fact_relationships
                        WHERE relationship_type NOT IN ('REVERSAL_OF','REPLACES','VOID_RELATION','CORRECTS')
                    """,
                    "red_without_reversal_count": """
                        SELECT count(*)
                        FROM invoice_facts i
                        WHERE i.invoice_status='RED'
                          AND NOT EXISTS (
                            SELECT 1 FROM fact_relationships r
                            WHERE r.source_fact_id=i.fact_id AND r.relationship_type='REVERSAL_OF'
                          )
                    """,
                    "voided_without_relation_count": """
                        SELECT count(*)
                        FROM invoice_facts i
                        WHERE i.invoice_status='VOIDED'
                          AND NOT EXISTS (
                            SELECT 1 FROM fact_relationships r
                            WHERE r.target_fact_id=i.fact_id AND r.relationship_type='VOID_RELATION'
                          )
                    """,
                    "reversal_semantic_mismatch_count": """
                        SELECT count(*)
                        FROM fact_relationships r
                        LEFT JOIN invoice_facts src ON src.fact_id=r.source_fact_id
                        LEFT JOIN invoice_facts dst ON dst.fact_id=r.target_fact_id
                        WHERE r.relationship_type='REVERSAL_OF'
                          AND (
                            src.fact_id IS NULL OR dst.fact_id IS NULL
                            OR src.invoice_status <> 'RED'
                            OR dst.invoice_status = 'RED'
                            OR src.seller_party_id IS DISTINCT FROM dst.seller_party_id
                            OR src.buyer_party_id IS DISTINCT FROM dst.buyer_party_id
                            OR src.currency IS DISTINCT FROM dst.currency
                          )
                    """,
                    "void_semantic_mismatch_count": """
                        SELECT count(*)
                        FROM fact_relationships r
                        LEFT JOIN invoice_facts dst ON dst.fact_id=r.target_fact_id
                        WHERE r.relationship_type='VOID_RELATION'
                          AND (dst.fact_id IS NULL OR dst.invoice_status <> 'VOIDED')
                    """,
                    "relationship_cycle_count": """
                        WITH RECURSIVE walk AS (
                            SELECT source_fact_id AS start_id,
                                   target_fact_id AS current_id,
                                   ARRAY[source_fact_id, target_fact_id] AS path,
                                   false AS cycle
                            FROM fact_relationships
                            UNION ALL
                            SELECT w.start_id,
                                   r.target_fact_id,
                                   w.path || r.target_fact_id,
                                   r.target_fact_id = ANY(w.path) AS cycle
                            FROM walk w
                            JOIN fact_relationships r ON r.source_fact_id=w.current_id
                            WHERE NOT w.cycle
                        )
                        SELECT count(*) FROM walk WHERE cycle
                    """,
                }
                nonblocking_counts = {"relationship_count", "red_invoice_count", "voided_invoice_count"}
                for name, sql in checks.items():
                    value = int(conn.execute(text(sql)).scalar_one())
                    evidence[name] = value
                    if name not in nonblocking_counts and value:
                        failures.append(f"{name}={value}")
        finally:
            conn.rollback()
    engine.dispose()

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    result = run()
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.json_path:
        Path(args.json_path).write_text(payload + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
