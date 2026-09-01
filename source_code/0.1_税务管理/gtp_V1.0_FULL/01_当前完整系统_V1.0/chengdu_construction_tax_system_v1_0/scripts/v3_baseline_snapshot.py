#!/usr/bin/env python3
"""V3 Task 02 — capture a deterministic pre-migration business baseline.

The snapshot is read-only and records counts/sums plus entity-period and
project-period tax-basis measures used by later migration gates.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def _db_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    url = make_url(value)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("V3 baseline snapshot is PostgreSQL-only")
    return value


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _rows(conn, sql: str) -> list[dict]:
    return [
        {key: _json_value(value) for key, value in row._mapping.items()}
        for row in conn.execute(text(sql))
    ]


def capture() -> dict:
    engine = create_engine(_db_url(), future=True, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            summary = _rows(
                conn,
                """
                SELECT
                  (SELECT COUNT(*) FROM invoices) AS invoice_count,
                  (SELECT COALESCE(SUM(net),0) FROM invoices) AS invoice_net_sum,
                  (SELECT COALESCE(SUM(vat),0) FROM invoices) AS invoice_vat_sum,
                  (SELECT COUNT(*) FROM contracts) AS contract_count,
                  (SELECT COUNT(*) FROM cashflows) AS cashflow_count,
                  (SELECT COUNT(*) FROM real_costs) AS real_cost_count,
                  (SELECT COUNT(*) FROM fulfillment) AS fulfillment_count
                """,
            )[0]
            entity_period = _rows(
                conn,
                """
                WITH inv AS (
                  SELECT entity_code, period,
                    COALESCE(SUM(net) FILTER (WHERE direction='out'),0) AS revenue,
                    COALESCE(SUM(vat) FILTER (WHERE direction='out'),0) AS output_vat,
                    COALESCE(SUM(vat) FILTER (WHERE direction='in' AND deductible),0) AS input_vat
                  FROM invoices
                  GROUP BY entity_code, period
                ), costs AS (
                  SELECT entity_code, period, COALESCE(SUM(amount),0) AS real_cost
                  FROM real_costs
                  GROUP BY entity_code, period
                ), keys AS (
                  SELECT entity_code, period FROM inv
                  UNION
                  SELECT entity_code, period FROM costs
                )
                SELECT k.entity_code, k.period,
                  COALESCE(i.revenue,0) AS revenue,
                  COALESCE(i.input_vat,0) AS input_vat,
                  COALESCE(i.output_vat,0) AS output_vat,
                  COALESCE(c.real_cost,0) AS real_cost
                FROM keys k
                LEFT JOIN inv i USING(entity_code, period)
                LEFT JOIN costs c USING(entity_code, period)
                ORDER BY k.entity_code, k.period
                """,
            )
            project_period = _rows(
                conn,
                """
                WITH inv AS (
                  SELECT project_id, period,
                    COALESCE(SUM(net) FILTER (WHERE direction='out'),0) AS revenue,
                    COALESCE(SUM(vat) FILTER (WHERE direction='out'),0) AS output_vat,
                    COALESCE(SUM(vat) FILTER (WHERE direction='in' AND deductible),0) AS input_vat
                  FROM invoices
                  GROUP BY project_id, period
                ), costs AS (
                  SELECT project_id, period, COALESCE(SUM(amount),0) AS real_cost
                  FROM real_costs
                  GROUP BY project_id, period
                ), keys AS (
                  SELECT project_id, period FROM inv
                  UNION
                  SELECT project_id, period FROM costs
                )
                SELECT k.project_id, p.code AS project_code, k.period,
                  COALESCE(i.revenue,0) AS revenue,
                  COALESCE(i.input_vat,0) AS input_vat,
                  COALESCE(i.output_vat,0) AS output_vat,
                  COALESCE(c.real_cost,0) AS real_cost
                FROM keys k
                JOIN projects p ON p.id=k.project_id
                LEFT JOIN inv i USING(project_id, period)
                LEFT JOIN costs c USING(project_id, period)
                ORDER BY k.project_id, k.period
                """,
            )
            link_count = None
            exists = conn.execute(
                text("SELECT to_regclass('public.real_cost_invoice_links')")
            ).scalar()
            if exists:
                link_count = int(
                    conn.execute(text("SELECT COUNT(*) FROM real_cost_invoice_links")).scalar() or 0
                )
            revisions = [
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version_tax ORDER BY version_num"))
            ]
        finally:
            conn.exec_driver_sql("ROLLBACK")
    engine.dispose()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "alembic_heads": revisions,
        "summary": summary,
        "entity_period": entity_period,
        "project_period": project_period,
        "real_cost_invoice_links_count": link_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="output JSON path")
    args = parser.parse_args()
    payload = capture()
    default_name = f"migration_baseline_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    target = Path(args.output or default_name)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
