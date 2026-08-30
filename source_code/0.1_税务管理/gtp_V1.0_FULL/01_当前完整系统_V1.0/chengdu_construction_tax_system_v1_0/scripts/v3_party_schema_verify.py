#!/usr/bin/env python3
"""Read-only verification for V3 Task 05 Party/Taxpayer schema.

Run only after revisions 74/75 have been applied to a disposable or approved
PostgreSQL target.  This script never mutates data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

EXPECTED_COLUMNS = {
    "source_documents": {
        "id", "source_system", "external_document_id", "filename", "mime_type",
        "file_sha256", "document_type", "received_at", "processed_at", "source_uri", "status",
    },
    "parties": {"id", "code", "name", "short_name", "party_type", "active"},
    "internal_entities": {
        "party_id", "canonical_code", "business_role", "legal_entity", "parent_party_id", "active",
    },
    "party_identifiers": {
        "id", "party_id", "identifier_type", "identifier_value", "source_system", "active",
    },
    "party_tax_profiles": {
        "id", "party_id", "tax_type", "reporting_party_id", "taxpayer_category",
        "tax_registration_id", "effective_from", "effective_to", "rule_version", "source", "reviewed",
    },
}


def _url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("V3 Party schema verification is PostgreSQL-only")
    return value


def run() -> dict:
    engine = create_engine(_url(), future=True, pool_pre_ping=True)
    failures: list[str] = []
    details: dict[str, object] = {}
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            inspector = inspect(conn)
            tables = set(inspector.get_table_names(schema="public"))
            for table, expected in EXPECTED_COLUMNS.items():
                if table not in tables:
                    failures.append(f"missing table: {table}")
                    continue
                columns = {row["name"] for row in inspector.get_columns(table, schema="public")}
                missing = sorted(expected - columns)
                if missing:
                    failures.append(f"{table}: missing columns {missing}")
                details[table] = {"column_count": len(columns), "missing": missing}

            external_columns = {
                row["name"]: row
                for row in inspector.get_columns("external_parties", schema="public")
            }
            for column in ("party_id", "industry"):
                if column not in external_columns:
                    failures.append(f"external_parties.{column}: missing")
            if "party_id" in external_columns and not external_columns["party_id"]["nullable"]:
                failures.append("external_parties.party_id must remain nullable before Task 06 backfill")

            tax_head = {
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
                if row[0]
            }
            details["alembic_version_tax"] = sorted(tax_head)
            if tax_head != {"75_v3_taxpayer_profiles"}:
                failures.append(
                    "Tax Alembic head must be 75_v3_taxpayer_profiles after Task 05 schema apply; "
                    f"found {sorted(tax_head)}"
                )

            exclusion = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_constraint c
                    JOIN pg_class t ON t.oid=c.conrelid
                    JOIN pg_namespace n ON n.oid=t.relnamespace
                    WHERE n.nspname='public'
                      AND t.relname='party_tax_profiles'
                      AND c.conname='ex_party_tax_profiles_no_overlap'
                      AND c.contype='x'
                    """
                )
            ).scalar_one_or_none()
            if exclusion != 1:
                failures.append("party_tax_profiles exclusion constraint is missing")

            external_party_count = int(
                conn.execute(text("SELECT COUNT(*) FROM external_parties")).scalar_one()
            )
            linked_external_count = int(
                conn.execute(
                    text("SELECT COUNT(*) FROM external_parties WHERE party_id IS NOT NULL")
                ).scalar_one()
            )
            details["external_party_bridge"] = {
                "legacy_rows": external_party_count,
                "linked_rows": linked_external_count,
                "note": "Task 05 does not require backfill; Task 06 owns 100% linkage.",
            }
        finally:
            conn.rollback()
    engine.dispose()
    return {"status": "PASS" if not failures else "FAIL", "failures": failures, "details": details}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    result = run()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
