#!/usr/bin/env python3
"""Read-only Gate S09 verifier for evidence-aware Invoice Fact validation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.domain.invoice.validation import (  # noqa: E402
    LEGACY_MIGRATION_IDENTITY_VERSION,
    RULESET_VERSION,
    evaluate_invoice_evidence,
)
from scripts.v3.invoice_validation import (  # noqa: E402
    EXPECTED_HEAD,
    RESULT_KIND,
    _fetch_snapshots,
)


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S09 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    script_location = Path(cfg.get_main_option("script_location"))
    if not script_location.is_absolute():
        cfg.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def _read_result(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Task 09 result must be a JSON object")
    if payload.get("kind") != RESULT_KIND or int(payload.get("version", 0)) != 1:
        raise RuntimeError("unsupported Task 09 result format")
    return payload


def _chunks(values: list[int], size: int = 500):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def run(result_path: str) -> dict[str, Any]:
    result = _read_result(result_path)
    failures: list[str] = []
    selected_ids = sorted({int(value) for value in result.get("selected_ids", [])})
    evidence: dict[str, Any] = {
        "selected_ids": selected_ids,
        "selected_count": len(selected_ids),
        "ruleset_version": RULESET_VERSION,
    }
    if not selected_ids:
        return {
            "status": "FAIL",
            "failures": ["Task 09 result selected_ids is empty"],
            "evidence": evidence,
        }
    if result.get("ruleset_version") != RULESET_VERSION:
        failures.append(
            f"result ruleset={result.get('ruleset_version')!r}, expected={RULESET_VERSION!r}"
        )

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            database = str(conn.execute(text("SELECT current_database()" )).scalar_one())
            evidence["database"] = database
            if result.get("database") != database:
                failures.append(
                    f"result database mismatch: result={result.get('database')!r} actual={database!r}"
                )

            db_heads = {
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
                if row[0]
            }
            disk_heads = _disk_heads()
            evidence["alembic_db_heads"] = sorted(db_heads)
            evidence["alembic_disk_heads"] = sorted(disk_heads)
            if db_heads != disk_heads:
                failures.append(
                    f"Tax Alembic head mismatch: db={sorted(db_heads)} disk={sorted(disk_heads)}"
                )
            if disk_heads != {EXPECTED_HEAD}:
                failures.append(
                    f"Task 09 must not consume a new revision: expected {EXPECTED_HEAD}, got {sorted(disk_heads)}"
                )

            rows = _fetch_snapshots(conn, ids=selected_ids)
            by_id = {row.fact_id: row for row in rows}
            result_rows = result.get("results", [])
            if not isinstance(result_rows, list):
                failures.append("Task 09 result.results must be a list")
                result_rows = []
            result_by_id = {
                int(row["fact_id"]): row
                for row in result_rows
                if isinstance(row, dict) and row.get("fact_id") is not None
            }
            if set(result_by_id) != set(selected_ids):
                failures.append(
                    f"result row coverage mismatch: result={sorted(result_by_id)} selected={selected_ids}"
                )

            selected_status_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
            for fact_id in selected_ids:
                snapshot = by_id[fact_id]
                decision = evaluate_invoice_evidence(snapshot)
                stored = snapshot.current_validation_status
                recorded = result_by_id.get(fact_id)
                if stored != decision.desired_status:
                    failures.append(
                        f"Fact {fact_id}: stored status {stored} != deterministic {decision.desired_status}"
                    )
                if stored in selected_status_counts:
                    selected_status_counts[stored] += 1
                if recorded is not None:
                    if str(recorded.get("after_status")) != stored:
                        failures.append(
                            f"Fact {fact_id}: result after_status={recorded.get('after_status')} db={stored}"
                        )
                    expected_codes = [item.code for item in decision.findings]
                    recorded_codes = [str(value) for value in recorded.get("finding_codes", [])]
                    if recorded_codes != expected_codes:
                        failures.append(
                            f"Fact {fact_id}: finding codes changed after APPLY"
                        )

            evidence["selected_status_counts"] = selected_status_counts

            legacy_valid_count = int(
                conn.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM facts f
                        JOIN invoice_facts i ON i.fact_id=f.id
                        WHERE f.is_current
                          AND f.validation_status='VALID'
                          AND i.invoice_identity_version=:legacy_version
                        """
                    ),
                    {"legacy_version": LEGACY_MIGRATION_IDENTITY_VERSION},
                ).scalar_one()
            )
            evidence["legacy_migration_valid_count"] = legacy_valid_count
            if legacy_valid_count:
                failures.append(
                    f"LEGACY_MIGRATION_V1 Facts promoted to VALID: {legacy_valid_count}"
                )

            valid_ids = [
                int(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT f.id
                        FROM facts f
                        JOIN invoice_facts i ON i.fact_id=f.id
                        WHERE f.fact_type='INVOICE'
                          AND f.is_current
                          AND f.validation_status='VALID'
                        ORDER BY f.id
                        """
                    )
                )
            ]
            evidence["global_valid_invoice_count"] = len(valid_ids)
            invalid_valid_ids: list[int] = []
            for batch in _chunks(valid_ids):
                for snapshot in _fetch_snapshots(conn, ids=batch):
                    if evaluate_invoice_evidence(snapshot).desired_status != "VALID":
                        invalid_valid_ids.append(snapshot.fact_id)
            evidence["globally_invalid_valid_fact_ids"] = invalid_valid_ids
            if invalid_valid_ids:
                failures.append(
                    f"current VALID Invoice Facts fail Task 09 evidence rules: {invalid_valid_ids}"
                )

            task08_fact_ids = [
                int(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT DISTINCT m.invoice_fact_id
                        FROM legacy_invoice_map m
                        JOIN invoice_facts i ON i.fact_id=m.invoice_fact_id
                        WHERE m.invoice_fact_id IS NOT NULL
                          AND i.invoice_identity_version=:legacy_version
                        ORDER BY m.invoice_fact_id
                        """
                    ),
                    {"legacy_version": LEGACY_MIGRATION_IDENTITY_VERSION},
                )
            ]
            evidence["task08_migration_fact_ids"] = task08_fact_ids
            task08_valid_ids = [
                int(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT DISTINCT m.invoice_fact_id
                        FROM legacy_invoice_map m
                        JOIN facts f ON f.id=m.invoice_fact_id
                        JOIN invoice_facts i ON i.fact_id=m.invoice_fact_id
                        WHERE m.invoice_fact_id IS NOT NULL
                          AND i.invoice_identity_version=:legacy_version
                          AND f.validation_status='VALID'
                        ORDER BY m.invoice_fact_id
                        """
                    ),
                    {"legacy_version": LEGACY_MIGRATION_IDENTITY_VERSION},
                )
            ]
            evidence["task08_migration_valid_fact_ids"] = task08_valid_ids
            if task08_valid_ids:
                failures.append(
                    f"Task 08 migration Facts were incorrectly promoted to VALID: {task08_valid_ids}"
                )
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
    parser.add_argument("--result", required=True, help="Task 09 APPLY result JSON")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    result = run(args.result)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.json_path:
        Path(args.json_path).write_text(payload + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
