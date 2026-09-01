#!/usr/bin/env python3
"""Read-only Gate S11 verifier for ContractFact / FulfillmentFact."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
EXPECTED_HEAD = "81_v3_contract_fulfillment_facts"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S11 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    location = Path(cfg.get_main_option("script_location"))
    if not location.is_absolute():
        cfg.set_main_option("script_location", str(ROOT / location))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def _db_heads(conn) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
        if row[0]
    }


def _count(conn, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar_one())


def run() -> dict[str, Any]:
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    failures: list[str] = []
    evidence: dict[str, Any] = {}

    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            db_heads = _db_heads(conn)
            disk_heads = _disk_heads()
            evidence["database"] = str(conn.execute(text("SELECT current_database()" )).scalar_one())
            evidence["alembic_db_heads"] = sorted(db_heads)
            evidence["alembic_disk_heads"] = sorted(disk_heads)
            if db_heads != {EXPECTED_HEAD} or disk_heads != {EXPECTED_HEAD}:
                failures.append(
                    f"Task11 head mismatch: db={sorted(db_heads)} disk={sorted(disk_heads)} expected={EXPECTED_HEAD}"
                )

            inspector = inspect(conn)
            tables = set(inspector.get_table_names(schema="public"))
            for table_name in ("contract_facts", "fulfillment_facts"):
                if table_name not in tables:
                    failures.append(f"missing table {table_name}")

            if "contract_facts" in tables:
                contract_columns = {
                    row["name"] for row in inspector.get_columns("contract_facts", schema="public")
                }
            else:
                contract_columns = set()
            if "fulfillment_facts" in tables:
                fulfillment_columns = {
                    row["name"] for row in inspector.get_columns("fulfillment_facts", schema="public")
                }
            else:
                fulfillment_columns = set()

            contract_forbidden = sorted(
                {"project_id", "internal_trade", "buyer_code", "seller_code"} & contract_columns
            )
            fulfillment_forbidden = sorted(
                {"project_id", "counterparty_code", "evidence_complete", "internal_trade"}
                & fulfillment_columns
            )
            evidence["contract_prohibited_columns"] = contract_forbidden
            evidence["fulfillment_prohibited_columns"] = fulfillment_forbidden
            if contract_forbidden:
                failures.append(f"contract_facts contains prohibited columns: {contract_forbidden}")
            if fulfillment_forbidden:
                failures.append(
                    f"fulfillment_facts contains prohibited columns: {fulfillment_forbidden}"
                )

            if "contract_facts" in tables:
                evidence["contract_fact_count"] = _count(conn, "SELECT count(*) FROM contract_facts")
                evidence["contract_wrong_fact_type_count"] = _count(
                    conn,
                    """
                    SELECT count(*) FROM contract_facts c
                    LEFT JOIN facts f ON f.id=c.fact_id
                    WHERE f.id IS NULL OR f.fact_type <> 'CONTRACT'
                    """,
                )
                evidence["contract_same_party_count"] = _count(
                    conn,
                    """
                    SELECT count(*) FROM contract_facts
                    WHERE buyer_party_id IS NOT NULL
                      AND seller_party_id IS NOT NULL
                      AND buyer_party_id=seller_party_id
                    """,
                )
                evidence["derived_internal_trade_count"] = _count(
                    conn,
                    """
                    SELECT count(*)
                    FROM contract_facts c
                    JOIN internal_entities ib ON ib.party_id=c.buyer_party_id
                    JOIN internal_entities iseller ON iseller.party_id=c.seller_party_id
                    """,
                )
                if evidence["contract_wrong_fact_type_count"]:
                    failures.append("contract_facts contains orphan/wrong-type Fact rows")
                if evidence["contract_same_party_count"]:
                    failures.append("contract_facts contains same buyer/seller Party")

            if "fulfillment_facts" in tables:
                evidence["fulfillment_fact_count"] = _count(
                    conn, "SELECT count(*) FROM fulfillment_facts"
                )
                evidence["independent_fulfillment_count"] = _count(
                    conn,
                    "SELECT count(*) FROM fulfillment_facts WHERE contract_fact_id IS NULL",
                )
                evidence["fulfillment_wrong_fact_type_count"] = _count(
                    conn,
                    """
                    SELECT count(*) FROM fulfillment_facts ff
                    LEFT JOIN facts f ON f.id=ff.fact_id
                    WHERE f.id IS NULL OR f.fact_type <> 'FULFILLMENT'
                    """,
                )
                evidence["fulfillment_same_party_count"] = _count(
                    conn,
                    """
                    SELECT count(*) FROM fulfillment_facts
                    WHERE performing_party_id IS NOT NULL
                      AND receiving_party_id IS NOT NULL
                      AND performing_party_id=receiving_party_id
                    """,
                )
                evidence["invalid_contract_link_count"] = _count(
                    conn,
                    """
                    SELECT count(*)
                    FROM fulfillment_facts ff
                    LEFT JOIN contract_facts c ON c.fact_id=ff.contract_fact_id
                    WHERE ff.contract_fact_id IS NOT NULL AND c.fact_id IS NULL
                    """,
                )
                if evidence["fulfillment_wrong_fact_type_count"]:
                    failures.append("fulfillment_facts contains orphan/wrong-type Fact rows")
                if evidence["fulfillment_same_party_count"]:
                    failures.append("fulfillment_facts contains same performing/receiving Party")
                if evidence["invalid_contract_link_count"]:
                    failures.append("fulfillment_facts contains invalid ContractFact links")
        finally:
            conn.exec_driver_sql("ROLLBACK")
    engine.dispose()
    return {"status": "FAIL" if failures else "PASS", "failures": failures, "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    result = run()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
