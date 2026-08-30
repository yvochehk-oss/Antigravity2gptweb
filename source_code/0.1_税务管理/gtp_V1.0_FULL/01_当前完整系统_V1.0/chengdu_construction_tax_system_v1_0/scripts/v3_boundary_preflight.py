#!/usr/bin/env python3
"""V3 Step 0.2 preflight for legacy party-code references.

Reports length risk, sentinels and unresolved party codes before/after the
VARCHAR(64) boundary hotfix.  Read-only; unresolved values are surfaced for
manual review rather than guessed or rewritten.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

COLUMNS = [
    ("contracts", "buyer_code"),
    ("contracts", "seller_code"),
    ("invoices", "entity_code"),
    ("invoices", "counterparty_code"),
    ("cashflows", "entity_code"),
    ("cashflows", "counterparty_code"),
    ("fulfillment", "counterparty_code"),
    ("real_costs", "entity_code"),
    ("real_costs", "counterparty_code"),
]


def _url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    url = make_url(value)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("V3 boundary preflight is PostgreSQL-only")
    return value


def run() -> dict:
    engine = create_engine(_url(), future=True, pool_pre_ping=True)
    results = []
    hard_failures = []
    review_items = []
    with engine.connect() as conn:
        for table, column in COLUMNS:
            row = conn.execute(
                text(
                    f"""
                    WITH valueset AS (
                      SELECT NULLIF(BTRIM("{column}"), '') AS code
                      FROM "{table}"
                    )
                    SELECT
                      COALESCE(MAX(LENGTH(code)), 0) AS max_length,
                      COUNT(*) FILTER (WHERE code IS NULL) AS blank_count,
                      COUNT(*) FILTER (WHERE UPPER(code)='UNKNOWN') AS unknown_count,
                      COUNT(*) FILTER (WHERE code IN ('A','B','C','D')) AS virtual_role_count,
                      COUNT(*) FILTER (WHERE LENGTH(code) > 64) AS over_64_count,
                      COUNT(*) FILTER (
                        WHERE code IS NOT NULL
                          AND UPPER(code) <> 'UNKNOWN'
                          AND code NOT IN ('A','B','C','D')
                          AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.code=code)
                          AND NOT EXISTS (SELECT 1 FROM external_parties p WHERE p.code=code)
                      ) AS unresolved_count
                    FROM valueset
                    """
                )
            ).mappings().one()
            unresolved_sample = [
                str(value)
                for value in conn.execute(
                    text(
                        f"""
                        SELECT DISTINCT BTRIM(t."{column}") AS code
                        FROM "{table}" t
                        WHERE NULLIF(BTRIM(t."{column}"), '') IS NOT NULL
                          AND UPPER(BTRIM(t."{column}")) <> 'UNKNOWN'
                          AND BTRIM(t."{column}") NOT IN ('A','B','C','D')
                          AND NOT EXISTS (
                            SELECT 1 FROM entities e WHERE e.code=BTRIM(t."{column}")
                          )
                          AND NOT EXISTS (
                            SELECT 1 FROM external_parties p WHERE p.code=BTRIM(t."{column}")
                          )
                        ORDER BY code
                        LIMIT 20
                        """
                    )
                ).scalars().all()
            ]
            item = {
                "table": table,
                "column": column,
                **{key: int(value or 0) for key, value in row.items()},
                "unresolved_sample": unresolved_sample,
            }
            results.append(item)
            if item["over_64_count"]:
                hard_failures.append(f"{table}.{column}: {item['over_64_count']} values exceed 64 chars")
            if any(
                item[key]
                for key in ("unknown_count", "virtual_role_count", "unresolved_count")
            ):
                review_items.append(
                    f"{table}.{column}: UNKNOWN={item['unknown_count']}, "
                    f"role={item['virtual_role_count']}, unresolved={item['unresolved_count']}"
                )
    engine.dispose()
    status = "FAIL" if hard_failures else ("WARNING" if review_items else "PASS")
    return {
        "status": status,
        "failures": hard_failures,
        "review_items": review_items,
        "columns": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--strict", action="store_true", help="treat review items as failure")
    args = parser.parse_args()
    result = run()
    if args.strict and result["status"] == "WARNING":
        result["status"] = "FAIL"
        result["failures"].append("strict mode: unresolved/sentinel party codes require review")
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
