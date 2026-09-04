#!/usr/bin/env python3
"""Fail-closed repair for the six reviewed Phase A legacy party references.

The source rows and canonical targets are evidence-backed and intentionally
explicit.  The script defaults to a read-only dry run.  ``--apply`` requires
the caller to confirm the exact database name and refuses to continue if the
source rows, master identities, or occurrence counts have changed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


COLUMNS = (
    ("contracts", "buyer_code"),
    ("contracts", "seller_code"),
    ("invoices", "entity_code"),
    ("invoices", "counterparty_code"),
    ("cashflows", "entity_code"),
    ("cashflows", "counterparty_code"),
    ("fulfillment", "counterparty_code"),
    ("real_costs", "entity_code"),
    ("real_costs", "counterparty_code"),
)

MASTERS = {
    "E0": {
        "name": "成都市天府新区金融城投公司",
        "tax_id": "91510100MA61AAAA11",
    },
    "EB": {
        "name": "攀钢集团攀枝花钢钒物资销售有限公司",
        "tax_id": "91510400MA61EEEE77",
    },
    "ED": {
        "name": "重庆重交大件起重吊装工程有限公司",
        "tax_id": "91500100MA61GGGG99",
    },
}

TARGETS: tuple[dict[str, Any], ...] = (
    {
        "table": "contracts",
        "column": "buyer_code",
        "source": "EXT-TF",
        "target": "E0",
        "where": "contract_no=:key AND project_id=(SELECT id FROM projects WHERE code='CD-TF-001')",
        "key": "CDTF-MAIN-2026-01",
        "evidence": "RAG contract CDTF-MAIN-2026-01 and external alias registry",
    },
    {
        "table": "contracts",
        "column": "buyer_code",
        "source": "EXT-TF",
        "target": "E0",
        "where": "contract_no=:key AND project_id=(SELECT id FROM projects WHERE code='CD-TF-001')",
        "key": "CDTF-MAIN-2023-01",
        "evidence": "RAG contract CDTF-MAIN-2023-01 and external alias registry",
    },
    {
        "table": "invoices",
        "column": "counterparty_code",
        "source": "EXT-TF",
        "target": "E0",
        "where": "invoice_no=:key AND project_id=(SELECT id FROM projects WHERE code='CD-TF-001')",
        "key": "OUT-TF-001",
        "evidence": "A08 output invoice to the Tianfu project owner",
    },
    {
        "table": "cashflows",
        "column": "counterparty_code",
        "source": "EXT-001",
        "target": "EB",
        "where": "bank_reference=:key AND project_id=(SELECT id FROM projects WHERE code='CD-TF-001')",
        "key": "EBNK20260322208222",
        "evidence": "Bank receipt names Panzhihua Steel Vanadium Materials Sales Co.",
    },
    {
        "table": "cashflows",
        "column": "counterparty_code",
        "source": "EXT-001",
        "target": "EB",
        "where": "bank_reference=:key AND project_id=(SELECT id FROM projects WHERE code='CD-TF-001')",
        "key": "EBNK20230825555498",
        "evidence": "Bank receipt names Panzhihua Steel Vanadium Materials Sales Co.",
    },
    {
        "table": "real_costs",
        "column": "counterparty_code",
        "source": "EXT-CRANE",
        "target": "ED",
        "where": (
            "project_id=(SELECT id FROM projects WHERE code='CD-TF-001') "
            "AND entity_code='A08' AND period='2026-03' AND amount=24940000.00 "
            "AND note='重庆巨力重型起重设备吊装'"
        ),
        "key": "CD-TF-001/A08/2026-03/24940000.00",
        "evidence": "RAG crane contract, invoice and receipt identify tax ID 91500100MA61GGGG99",
    },
)


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    url = make_url(value)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise RuntimeError("Phase A party-code repair is PostgreSQL-only")
    return value


def _validate_masters(conn) -> None:
    for code, expected in MASTERS.items():
        row = conn.execute(
            text("SELECT name, tax_id, active FROM external_parties WHERE code=:code"),
            {"code": code},
        ).mappings().one_or_none()
        if row is None:
            raise RuntimeError(f"canonical ExternalParty is missing: {code}")
        if row["name"] != expected["name"] or row["tax_id"] != expected["tax_id"]:
            raise RuntimeError(
                f"canonical ExternalParty identity mismatch for {code}: "
                f"name={row['name']!r}, tax_id={row['tax_id']!r}"
            )
        if not row["active"]:
            raise RuntimeError(f"canonical ExternalParty is inactive: {code}")


def _alias_occurrences(conn) -> dict[str, int]:
    aliases = sorted({str(item["source"]) for item in TARGETS})
    result = {alias: 0 for alias in aliases}
    for table, column in COLUMNS:
        rows = conn.execute(
            text(
                f'SELECT BTRIM("{column}") AS code, COUNT(*) AS count '
                f'FROM "{table}" WHERE BTRIM("{column}") = ANY(:aliases) '
                f'GROUP BY BTRIM("{column}")'
            ),
            {"aliases": aliases},
        ).mappings()
        for row in rows:
            result[str(row["code"])] += int(row["count"])
    return result


def run(*, apply: bool, confirm_database: str | None) -> dict[str, Any]:
    database_url = _database_url()
    database_name = make_url(database_url).database or ""
    if apply and confirm_database != database_name:
        raise RuntimeError(
            "--apply requires --confirm-database equal to the exact DATABASE_URL database name"
        )

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    changes: list[dict[str, Any]] = []
    expected_counts = {"EXT-TF": 3, "EXT-001": 2, "EXT-CRANE": 1}
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            if not apply:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            _validate_masters(conn)
            before_counts = _alias_occurrences(conn)
            if before_counts not in (expected_counts, {key: 0 for key in expected_counts}):
                raise RuntimeError(
                    f"reviewed alias occurrence set changed: {before_counts}, expected {expected_counts}"
                )

            for item in TARGETS:
                row = conn.execute(
                    text(
                        f'SELECT id, "{item["column"]}" AS current_code '
                        f'FROM "{item["table"]}" WHERE {item["where"]}'
                    ),
                    {"key": item["key"]},
                ).mappings().one_or_none()
                if row is None:
                    raise RuntimeError(
                        f"reviewed source row is missing: {item['table']}.{item['column']} {item['key']}"
                    )
                current = str(row["current_code"]).strip()
                if current not in {item["source"], item["target"]}:
                    raise RuntimeError(
                        f"reviewed source row changed unexpectedly: {item['table']} id={row['id']} "
                        f"has {current!r}"
                    )
                state = "already_canonical" if current == item["target"] else "ready"
                if apply and state == "ready":
                    updated = conn.execute(
                        text(
                            f'UPDATE "{item["table"]}" SET "{item["column"]}"=:target '
                            f'WHERE id=:id AND BTRIM("{item["column"]}")=:source'
                        ),
                        {
                            "target": item["target"],
                            "id": int(row["id"]),
                            "source": item["source"],
                        },
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError(
                            f"concurrent party-code change detected: {item['table']} id={row['id']}"
                        )
                    state = "updated"
                changes.append(
                    {
                        "table": item["table"],
                        "column": item["column"],
                        "row_id": int(row["id"]),
                        "from": item["source"],
                        "to": item["target"],
                        "state": state,
                        "evidence": item["evidence"],
                    }
                )

            after_counts = _alias_occurrences(conn)
            expected_after = {key: 0 for key in expected_counts}
            if apply and after_counts != expected_after:
                raise RuntimeError(f"party aliases remain after repair: {after_counts}")
            if apply:
                transaction.commit()
            else:
                transaction.rollback()
        except Exception:
            transaction.rollback()
            raise
        finally:
            engine.dispose()

    return {
        "status": "APPLIED" if apply else "READY",
        "database": database_name,
        "before_alias_counts": before_counts,
        "after_alias_counts": after_counts,
        "changes": changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    try:
        result = run(apply=args.apply, confirm_database=args.confirm_database)
        exit_code = 0
    except Exception as exc:
        result = {"status": "FAIL", "error": str(exc)}
        exit_code = 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
