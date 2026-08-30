#!/usr/bin/env python3
"""Gate S14b: Task14 VAT Ledger plus reviewed Output VAT completeness evidence."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

import entity_vat_gate_14 as base  # noqa: E402
import entity_vat_ledger_85 as ledger85  # noqa: E402

EXPECTED_HEAD = "85_v3_vat_output_period_assertions"
base.EXPECTED_HEAD = EXPECTED_HEAD
base._source_snapshot = ledger85._source_snapshot


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S14b is PostgreSQL-only")
    return value


def run() -> dict:
    result = base.run()
    failures = list(result.get("failures") or [])
    evidence = dict(result.get("evidence") or {})

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names(schema="public"))
        if "vat_output_period_assertions" not in tables:
            failures.append("missing vat_output_period_assertions")
            assertion_count = 0
            missing_assertion_ledger_ids = []
            assertion_mismatch_ledger_ids = []
        else:
            assertion_count = int(
                conn.execute(text("SELECT count(*) FROM vat_output_period_assertions WHERE reviewed")).scalar_one()
            )
            missing_assertion_ledger_ids = [
                int(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT l.id
                        FROM entity_vat_ledgers l
                        JOIN tax_period_states s
                          ON s.reporting_party_id=l.reporting_party_id
                         AND s.tax_type='VAT'
                         AND s.tax_period=l.tax_period
                         AND s.current_run_id=l.calculation_run_id
                        LEFT JOIN vat_output_period_assertions a
                          ON a.reporting_party_id=l.reporting_party_id
                         AND a.tax_period=l.tax_period
                         AND a.reviewed
                        WHERE a.id IS NULL
                        ORDER BY l.id
                        """
                    )
                )
            ]
            assertion_mismatch_ledger_ids = [
                int(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT l.id
                        FROM entity_vat_ledgers l
                        JOIN tax_period_states s
                          ON s.reporting_party_id=l.reporting_party_id
                         AND s.tax_type='VAT'
                         AND s.tax_period=l.tax_period
                         AND s.current_run_id=l.calculation_run_id
                        JOIN vat_output_period_assertions a
                          ON a.reporting_party_id=l.reporting_party_id
                         AND a.tax_period=l.tax_period
                         AND a.reviewed
                        WHERE l.output_vat IS DISTINCT FROM a.asserted_output_vat_total
                           OR a.asserted_output_vat_total IS DISTINCT FROM (
                               SELECT COALESCE(SUM(e.vat_amount),0)::numeric(18,2)
                               FROM output_vat_events e
                               WHERE e.reporting_party_id=l.reporting_party_id
                                 AND e.output_vat_period=l.tax_period
                                 AND e.event_status='CONFIRMED'
                           )
                        ORDER BY l.id
                        """
                    )
                )
            ]

        trigger_names = {
            str(row[0])
            for row in conn.execute(
                text(
                    """
                    SELECT tgname
                    FROM pg_trigger
                    WHERE NOT tgisinternal
                      AND tgrelid='entity_vat_ledgers'::regclass
                    """
                )
            )
        }
        required_trigger = "trg_v3_guard_entity_vat_ledger_output_completeness"
        if required_trigger not in trigger_names:
            failures.append(f"missing protection trigger: {required_trigger}")
        if missing_assertion_ledger_ids:
            failures.append(
                f"official VAT ledgers without reviewed Output VAT assertion: {missing_assertion_ledger_ids}"
            )
        if assertion_mismatch_ledger_ids:
            failures.append(
                f"official VAT ledgers whose Output VAT does not match reviewed completeness evidence: {assertion_mismatch_ledger_ids}"
            )

        evidence.update(
            {
                "reviewed_output_assertion_count": assertion_count,
                "official_ledger_missing_output_assertion_ids": missing_assertion_ledger_ids,
                "official_ledger_output_assertion_mismatch_ids": assertion_mismatch_ledger_ids,
                "output_completeness_trigger_present": required_trigger in trigger_names,
            }
        )
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
