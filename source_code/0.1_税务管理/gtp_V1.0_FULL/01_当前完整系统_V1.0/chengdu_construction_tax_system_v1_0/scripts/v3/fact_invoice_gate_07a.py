#!/usr/bin/env python3
"""Read-only PostgreSQL verifier for Task 07a Fact + Invoice schema."""
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
EXPECTED_TABLES = {
    "facts",
    "invoice_facts",
    "invoice_lines",
    "fact_provenance",
    "legacy_invoice_map",
}
FORBIDDEN_INVOICE_COLUMNS = {"direction", "internal_trade", "deductible", "project_id"}


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task 07a verifier is PostgreSQL-only")
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
            missing = sorted(EXPECTED_TABLES - tables)
            if missing:
                failures.append(f"missing Task 07a tables: {missing}")

            if "invoice_facts" in tables:
                invoice_columns = {
                    row["name"] for row in inspector.get_columns("invoice_facts", schema="public")
                }
                forbidden = sorted(FORBIDDEN_INVOICE_COLUMNS & invoice_columns)
                if forbidden:
                    failures.append(f"invoice_facts contains forbidden columns: {forbidden}")
                uniques = {
                    tuple(row.get("column_names") or [])
                    for row in inspector.get_unique_constraints("invoice_facts", schema="public")
                }
                if ("invoice_identity_key",) not in uniques:
                    failures.append("invoice_facts.invoice_identity_key is not UNIQUE")

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

            if not missing:
                checks = {
                    "invoice_fact_count": "SELECT count(*) FROM invoice_facts",
                    "orphan_invoice_fact_count": """
                        SELECT count(*) FROM invoice_facts i
                        LEFT JOIN facts f ON f.id=i.fact_id
                        WHERE f.id IS NULL
                    """,
                    "wrong_fact_type_count": """
                        SELECT count(*) FROM invoice_facts i
                        JOIN facts f ON f.id=i.fact_id
                        WHERE f.fact_type <> 'INVOICE'
                    """,
                    "duplicate_invoice_identity_count": """
                        SELECT count(*) FROM (
                            SELECT invoice_identity_key
                            FROM invoice_facts
                            GROUP BY invoice_identity_key
                            HAVING count(*) > 1
                        ) d
                    """,
                    "duplicate_current_business_identity_count": """
                        SELECT count(*) FROM (
                            SELECT business_identity_key
                            FROM facts
                            WHERE is_current
                            GROUP BY business_identity_key
                            HAVING count(*) > 1
                        ) d
                    """,
                    "invalid_valid_header_count": """
                        SELECT count(*)
                        FROM invoice_facts i
                        JOIN facts f ON f.id=i.fact_id
                        WHERE f.validation_status='VALID'
                          AND (
                            i.seller_party_id IS NULL
                            OR i.buyer_party_id IS NULL
                            OR i.seller_party_id=i.buyer_party_id
                            OR i.invoice_date IS NULL
                            OR i.net_amount IS NULL
                            OR i.vat_amount IS NULL
                            OR i.gross_amount IS NULL
                            OR abs((i.net_amount+i.vat_amount)-i.gross_amount) > 0.01
                          )
                    """,
                    "valid_invoice_line_mismatch_count": """
                        SELECT count(*)
                        FROM invoice_facts i
                        JOIN facts f ON f.id=i.fact_id
                        LEFT JOIN (
                            SELECT invoice_fact_id,
                                   count(*) AS line_count,
                                   coalesce(sum(net_amount),0) AS line_net,
                                   coalesce(sum(vat_amount),0) AS line_vat
                            FROM invoice_lines
                            GROUP BY invoice_fact_id
                        ) l ON l.invoice_fact_id=i.fact_id
                        WHERE f.validation_status='VALID'
                          AND (
                            coalesce(l.line_count,0)=0
                            OR abs(coalesce(l.line_net,0)-i.net_amount) > 0.01
                            OR abs(coalesce(l.line_vat,0)-i.vat_amount) > 0.01
                            OR abs((coalesce(l.line_net,0)+coalesce(l.line_vat,0))-i.gross_amount) > 0.01
                          )
                    """,
                }
                for name, sql in checks.items():
                    value = int(conn.execute(text(sql)).scalar_one())
                    evidence[name] = value
                    if name != "invoice_fact_count" and value:
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
