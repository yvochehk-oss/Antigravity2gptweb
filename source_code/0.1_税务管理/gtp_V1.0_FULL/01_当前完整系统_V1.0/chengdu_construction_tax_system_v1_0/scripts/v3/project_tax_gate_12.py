#!/usr/bin/env python3
"""Read-only Gate S12 verifier for Project Tax Treatment / Tax Prepayment Facts."""
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
EXPECTED_HEAD = "82_v3_project_tax_prepayment"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S12 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    config = Config(str(ROOT / "alembic.ini"))
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        config.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(config).get_heads())


def _scalar(conn, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar_one())


def run() -> dict[str, Any]:
    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    failures: list[str] = []

    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            inspector = inspect(conn)
            db_heads = {
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
                if row[0]
            }
            disk_heads = _disk_heads()
            if db_heads != {EXPECTED_HEAD}:
                failures.append(f"formal DB head must be {EXPECTED_HEAD}: {sorted(db_heads)}")
            if disk_heads != {EXPECTED_HEAD}:
                failures.append(f"disk head must be {EXPECTED_HEAD}: {sorted(disk_heads)}")

            tables = set(inspector.get_table_names(schema="public"))
            required = {"project_tax_treatments", "tax_prepayment_facts"}
            missing = sorted(required - tables)
            if missing:
                failures.append(f"missing Task12 tables: {missing}")

            treatment_columns = {
                row["name"]
                for row in inspector.get_columns("project_tax_treatments", schema="public")
            } if "project_tax_treatments" in tables else set()
            prepayment_columns = {
                row["name"]
                for row in inspector.get_columns("tax_prepayment_facts", schema="public")
            } if "tax_prepayment_facts" in tables else set()

            treatment_prohibited = sorted(
                treatment_columns
                & {"rate", "prepayment_rate", "cashflow_id", "bank_transaction_id"}
            )
            prepayment_prohibited = sorted(
                prepayment_columns
                & {
                    "rate",
                    "prepayment_rate",
                    "cashflow_id",
                    "bank_transaction_id",
                    "invoice_fact_id",
                    "recognized_revenue",
                    "cost_amount",
                }
            )
            if treatment_prohibited:
                failures.append(f"project_tax_treatments mixed/prohibited columns: {treatment_prohibited}")
            if prepayment_prohibited:
                failures.append(f"tax_prepayment_facts mixed/prohibited columns: {prepayment_prohibited}")

            treatment_count = _scalar(conn, "SELECT count(*) FROM project_tax_treatments") if required <= tables else 0
            prepayment_count = _scalar(conn, "SELECT count(*) FROM tax_prepayment_facts") if required <= tables else 0

            wrong_fact_type_count = _scalar(
                conn,
                """
                SELECT count(*)
                FROM tax_prepayment_facts p
                JOIN facts f ON f.id=p.fact_id
                WHERE f.fact_type <> 'TAX_PREPAYMENT'
                """,
            ) if required <= tables else 0
            if wrong_fact_type_count:
                failures.append(f"TaxPrepayment subtype rows with wrong Fact type: {wrong_fact_type_count}")

            invalid_period_count = _scalar(
                conn,
                "SELECT count(*) FROM tax_prepayment_facts WHERE EXTRACT(DAY FROM tax_period) <> 1",
            ) if required <= tables else 0
            if invalid_period_count:
                failures.append(f"TaxPrepayment rows with non-month-start tax_period: {invalid_period_count}")

            invalid_sign_count = _scalar(
                conn,
                """
                SELECT count(*)
                FROM tax_prepayment_facts
                WHERE (event_type='PREPAYMENT' AND tax_amount <= 0)
                   OR (event_type='REVERSAL' AND tax_amount >= 0)
                   OR (event_type='ADJUSTMENT' AND tax_amount = 0)
                """,
            ) if required <= tables else 0
            if invalid_sign_count:
                failures.append(f"TaxPrepayment rows with invalid event sign: {invalid_sign_count}")

            valid_without_treatment_count = _scalar(
                conn,
                """
                SELECT count(*)
                FROM tax_prepayment_facts p
                JOIN facts f ON f.id=p.fact_id
                WHERE f.validation_status='VALID' AND f.is_current
                  AND p.treatment_id IS NULL
                """,
            ) if required <= tables else 0
            if valid_without_treatment_count:
                failures.append(
                    f"current VALID TaxPrepayment Facts without treatment: {valid_without_treatment_count}"
                )

            valid_unreviewed_treatment_count = _scalar(
                conn,
                """
                SELECT count(*)
                FROM tax_prepayment_facts p
                JOIN facts f ON f.id=p.fact_id
                JOIN project_tax_treatments t ON t.id=p.treatment_id
                WHERE f.validation_status='VALID' AND f.is_current AND NOT t.reviewed
                """,
            ) if required <= tables else 0
            if valid_unreviewed_treatment_count:
                failures.append(
                    "current VALID TaxPrepayment Facts linked to unreviewed treatment: "
                    f"{valid_unreviewed_treatment_count}"
                )

            valid_treatment_mismatch_count = _scalar(
                conn,
                """
                SELECT count(*)
                FROM tax_prepayment_facts p
                JOIN facts f ON f.id=p.fact_id
                JOIN project_tax_treatments t ON t.id=p.treatment_id
                WHERE f.validation_status='VALID' AND f.is_current
                  AND (
                       t.project_id <> p.project_id
                    OR t.tax_type <> p.tax_type
                    OR t.reporting_party_id <> p.reporting_party_id
                    OR p.tax_event_date < t.effective_from
                    OR (t.effective_to IS NOT NULL AND p.tax_event_date > t.effective_to)
                  )
                """,
            ) if required <= tables else 0
            if valid_treatment_mismatch_count:
                failures.append(
                    "current VALID TaxPrepayment Facts with mismatched/ineffective treatment: "
                    f"{valid_treatment_mismatch_count}"
                )

            noncurrent_valid_count = _scalar(
                conn,
                """
                SELECT count(*)
                FROM tax_prepayment_facts p
                JOIN facts f ON f.id=p.fact_id
                WHERE f.validation_status='VALID' AND NOT f.is_current
                """,
            ) if required <= tables else 0

            evidence = {
                "database": make_url(database_url).database,
                "alembic_db_heads": sorted(db_heads),
                "alembic_disk_heads": sorted(disk_heads),
                "treatment_prohibited_columns": treatment_prohibited,
                "prepayment_prohibited_columns": prepayment_prohibited,
                "project_tax_treatment_count": treatment_count,
                "tax_prepayment_fact_count": prepayment_count,
                "wrong_fact_type_count": wrong_fact_type_count,
                "invalid_tax_period_count": invalid_period_count,
                "invalid_event_sign_count": invalid_sign_count,
                "valid_without_treatment_count": valid_without_treatment_count,
                "valid_unreviewed_treatment_count": valid_unreviewed_treatment_count,
                "valid_treatment_mismatch_count": valid_treatment_mismatch_count,
                "noncurrent_valid_fact_count": noncurrent_valid_count,
            }
        finally:
            conn.exec_driver_sql("ROLLBACK")
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
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
