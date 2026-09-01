#!/usr/bin/env python3
"""Read-only PostgreSQL verifier for one Task 08 legacy invoice pilot result."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import bindparam, create_engine, inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.domain.invoice.legacy_migration import MIGRATION_IDENTITY_VERSION  # noqa: E402
from scripts.v3.invoice_legacy_pilot import RESULT_KIND  # noqa: E402

EXPECTED_HEAD = "79_v3_legacy_invoice_pilot_bridge"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task 08 gate is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    script_location = Path(cfg.get_main_option("script_location"))
    if not script_location.is_absolute():
        cfg.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def _read_result(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Task 08 result must be a JSON object")
    if value.get("kind") != RESULT_KIND or int(value.get("version", 0)) != 1:
        raise RuntimeError("unsupported Task 08 result format")
    return value


def _rows_by_ids(conn, ids: list[int]) -> dict[int, dict[str, Any]]:
    stmt = text(
        """
        SELECT id, project_id, invoice_no, period, entity_code, direction,
               counterparty_code, category, net, vat, rate, deductible, note
        FROM invoices WHERE id IN :ids
        """
    ).bindparams(bindparam("ids", expanding=True))
    return {
        int(row.id): dict(row._mapping)
        for row in conn.execute(stmt, {"ids": ids})
    }


def _money(value: object) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


def run(result_path: str) -> dict[str, Any]:
    result = _read_result(result_path)
    selected_ids = sorted({int(value) for value in result.get("selected_ids", [])})
    failures: list[str] = []
    evidence: dict[str, Any] = {
        "selected_ids": selected_ids,
        "selected_count": len(selected_ids),
    }
    if not selected_ids:
        return {"status": "FAIL", "failures": ["result selected_ids is empty"], "evidence": evidence}
    if len(selected_ids) > 500:
        failures.append(f"pilot exceeds small-batch hard ceiling: selected={len(selected_ids)} > 500")

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

            actual_heads = {
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
                if row[0]
            }
            disk_heads = _disk_heads()
            evidence["alembic_db_heads"] = sorted(actual_heads)
            evidence["alembic_disk_heads"] = sorted(disk_heads)
            if actual_heads != disk_heads:
                failures.append(
                    f"Tax Alembic head mismatch: db={sorted(actual_heads)} disk={sorted(disk_heads)}"
                )
            if disk_heads != {EXPECTED_HEAD}:
                failures.append(
                    f"Task 08 expected head {EXPECTED_HEAD}, got {sorted(disk_heads)}"
                )

            inspector = inspect(conn)
            link_columns = {
                row["name"]
                for row in inspector.get_columns("real_cost_invoice_links", schema="public")
            }
            if "invoice_fact_id" not in link_columns:
                failures.append("real_cost_invoice_links.invoice_fact_id bridge missing")

            map_stmt = text(
                """
                SELECT legacy_invoice_id, invoice_fact_id, legacy_direction,
                       migration_status, reason
                FROM legacy_invoice_map
                WHERE legacy_invoice_id IN :ids
                ORDER BY legacy_invoice_id
                """
            ).bindparams(bindparam("ids", expanding=True))
            maps = [dict(row._mapping) for row in conn.execute(map_stmt, {"ids": selected_ids})]
            mapped_ids = {int(row["legacy_invoice_id"]) for row in maps}
            evidence["mapped_selected_count"] = len(mapped_ids)
            missing_map_ids = sorted(set(selected_ids) - mapped_ids)
            if missing_map_ids:
                failures.append(f"silent-drop/unmapped selected legacy ids: {missing_map_ids}")
            if len(maps) != len(selected_ids):
                failures.append(
                    f"legacy_invoice_map coverage mismatch: selected={len(selected_ids)} maps={len(maps)}"
                )

            total_map_count = int(
                conn.execute(text("SELECT count(*) FROM legacy_invoice_map")).scalar_one()
            )
            evidence["legacy_invoice_map_total"] = total_map_count
            if total_map_count != len(selected_ids):
                failures.append(
                    "Task 08 is a one-batch pilot but legacy_invoice_map contains rows "
                    f"outside the recorded result: total={total_map_count} selected={len(selected_ids)}"
                )

            legacy_rows = _rows_by_ids(conn, selected_ids)
            if set(legacy_rows) != set(selected_ids):
                failures.append("one or more selected legacy invoices no longer exist")

            map_by_id = {int(row["legacy_invoice_id"]): row for row in maps}
            expected_fact_ids: set[int] = set()
            actions = result.get("actions", [])
            if not isinstance(actions, list):
                failures.append("result actions must be a list")
                actions = []

            action_covered: list[int] = []
            for index, action in enumerate(actions):
                if not isinstance(action, dict):
                    failures.append(f"action[{index}] is not an object")
                    continue
                ids = [int(value) for value in action.get("legacy_ids", [])]
                action_covered.extend(ids)
                action_type = str(action.get("action") or "")
                status = str(action.get("migration_status") or "")
                fact_id_raw = action.get("invoice_fact_id")
                fact_id = int(fact_id_raw) if fact_id_raw is not None else None

                for legacy_id in ids:
                    mapped = map_by_id.get(legacy_id)
                    if mapped is None:
                        continue
                    if str(mapped["migration_status"]) != status:
                        failures.append(
                            f"legacy {legacy_id}: result status={status} db={mapped['migration_status']}"
                        )
                    db_fact_id = mapped["invoice_fact_id"]
                    if (int(db_fact_id) if db_fact_id is not None else None) != fact_id:
                        failures.append(
                            f"legacy {legacy_id}: result fact={fact_id} db={db_fact_id}"
                        )

                if action_type == "REVIEW_ONLY":
                    if status != "NEEDS_REVIEW" or fact_id is not None:
                        failures.append(
                            f"action[{index}] REVIEW_ONLY must be NEEDS_REVIEW with null fact"
                        )
                    continue

                if action_type == "MERGE_PAIR":
                    if len(ids) != 2 or status != "MERGED" or fact_id is None:
                        failures.append(
                            f"action[{index}] invalid MERGE_PAIR shape/status/fact"
                        )
                    else:
                        directions = {
                            str(legacy_rows[value].get("direction") or "").strip().lower()
                            for value in ids
                            if value in legacy_rows
                        }
                        if directions != {"in", "out"}:
                            failures.append(
                                f"action[{index}] MERGE_PAIR directions are not exact in/out: {directions}"
                            )
                        if len(ids) == 2 and all(value in legacy_rows for value in ids):
                            left, right = legacy_rows[ids[0]], legacy_rows[ids[1]]
                            for field in ("net", "vat", "rate"):
                                if Decimal(left[field] or 0) != Decimal(right[field] or 0):
                                    failures.append(
                                        f"action[{index}] MERGE_PAIR legacy {field} mismatch"
                                    )
                            if str(left.get("category") or "").strip().upper() != str(
                                right.get("category") or ""
                            ).strip().upper():
                                failures.append(
                                    f"action[{index}] MERGE_PAIR legacy category mismatch"
                                )
                elif action_type == "MIGRATE_SINGLE":
                    if len(ids) != 1 or status != "MIGRATED_SINGLE_PERSPECTIVE" or fact_id is None:
                        failures.append(
                            f"action[{index}] invalid MIGRATE_SINGLE shape/status/fact"
                        )
                else:
                    failures.append(f"action[{index}] unsupported action type {action_type!r}")

                if fact_id is not None:
                    expected_fact_ids.add(fact_id)
                    fact_row = conn.execute(
                        text(
                            """
                            SELECT f.fact_type, f.validation_status,
                                   i.invoice_identity_version, i.invoice_status,
                                   i.document_type, i.seller_party_id, i.buyer_party_id,
                                   i.net_amount, i.vat_amount, i.gross_amount,
                                   (SELECT count(*) FROM invoice_lines l
                                    WHERE l.invoice_fact_id=i.fact_id) AS line_count
                            FROM facts f
                            JOIN invoice_facts i ON i.fact_id=f.id
                            WHERE f.id=:fact_id
                            """
                        ),
                        {"fact_id": fact_id},
                    ).mappings().first()
                    if fact_row is None:
                        failures.append(f"action[{index}] invoice Fact {fact_id} missing")
                    else:
                        if fact_row["fact_type"] != "INVOICE":
                            failures.append(f"Fact {fact_id} has wrong fact_type")
                        if fact_row["validation_status"] != "NEEDS_REVIEW":
                            failures.append(
                                f"Task 08 Fact {fact_id} must remain NEEDS_REVIEW, got "
                                f"{fact_row['validation_status']}"
                            )
                        if fact_row["invoice_identity_version"] != MIGRATION_IDENTITY_VERSION:
                            failures.append(
                                f"Fact {fact_id} does not use migration-only identity version"
                            )
                        if fact_row["invoice_status"] is not None:
                            failures.append(
                                f"Task 08 Fact {fact_id} must not invent invoice_status"
                            )
                        if fact_row["document_type"] != "LEGACY_LEDGER_MIGRATION":
                            failures.append(
                                f"Task 08 Fact {fact_id} has unexpected document_type"
                            )
                        if int(fact_row["line_count"] or 0):
                            failures.append(
                                f"Task 08 Fact {fact_id} must not synthesize invoice_lines"
                            )
                        seller = action.get("seller_party_id")
                        buyer = action.get("buyer_party_id")
                        db_seller = fact_row["seller_party_id"]
                        db_buyer = fact_row["buyer_party_id"]
                        if seller is None or db_seller is None or int(db_seller) != int(seller):
                            failures.append(f"Fact {fact_id} seller_party_id mismatch")
                        if buyer is None or db_buyer is None or int(db_buyer) != int(buyer):
                            failures.append(f"Fact {fact_id} buyer_party_id mismatch")
                        if ids and min(ids) in legacy_rows:
                            source = legacy_rows[min(ids)]
                            net = _money(source["net"])
                            vat = _money(source["vat"])
                            if _money(fact_row["net_amount"]) != net:
                                failures.append(f"Fact {fact_id} net amount mismatch")
                            if _money(fact_row["vat_amount"]) != vat:
                                failures.append(f"Fact {fact_id} VAT amount mismatch")
                            if _money(fact_row["gross_amount"]) != net + vat:
                                failures.append(f"Fact {fact_id} gross amount mismatch")

            if sorted(action_covered) != selected_ids:
                failures.append(
                    f"result action coverage mismatch: actions={sorted(action_covered)} "
                    f"selected={selected_ids}"
                )

            result_fact_ids = {int(value) for value in result.get("created_fact_ids", [])}
            evidence["created_fact_ids"] = sorted(expected_fact_ids)
            if result_fact_ids != expected_fact_ids:
                failures.append(
                    f"created_fact_ids mismatch: result={sorted(result_fact_ids)} "
                    f"actions={sorted(expected_fact_ids)}"
                )

            all_migration_fact_ids = {
                int(row[0])
                for row in conn.execute(
                    text(
                        "SELECT fact_id FROM invoice_facts "
                        "WHERE invoice_identity_version=:version"
                    ),
                    {"version": MIGRATION_IDENTITY_VERSION},
                )
            }
            evidence["all_task08_migration_fact_ids"] = sorted(all_migration_fact_ids)
            if all_migration_fact_ids != expected_fact_ids:
                failures.append(
                    "migration-only InvoiceFacts exist outside the recorded Task 08 result: "
                    f"db={sorted(all_migration_fact_ids)} result={sorted(expected_fact_ids)}"
                )

            bridge_stmt = text(
                """
                SELECT l.invoice_id, l.invoice_fact_id, m.invoice_fact_id AS mapped_fact_id
                FROM real_cost_invoice_links l
                JOIN legacy_invoice_map m ON m.legacy_invoice_id=l.invoice_id
                WHERE l.invoice_id IN :ids
                ORDER BY l.invoice_id
                """
            ).bindparams(bindparam("ids", expanding=True))
            bridge_rows = [
                dict(row._mapping) for row in conn.execute(bridge_stmt, {"ids": selected_ids})
            ]
            evidence["real_cost_link_count_in_batch"] = len(bridge_rows)
            evidence["real_cost_links_bridged"] = sum(
                1 for row in bridge_rows if row["invoice_fact_id"] is not None
            )
            for row in bridge_rows:
                linked = row["invoice_fact_id"]
                mapped = row["mapped_fact_id"]
                if mapped is None:
                    if linked is not None:
                        failures.append(
                            f"legacy {row['invoice_id']}: review-only row has unexpected real-cost Fact bridge"
                        )
                elif linked is None or int(linked) != int(mapped):
                    failures.append(
                        f"legacy {row['invoice_id']}: real-cost bridge does not match legacy_invoice_map"
                    )

            duplicate_identity_count = int(
                conn.execute(
                    text(
                        """
                        SELECT count(*) FROM (
                            SELECT invoice_identity_key
                            FROM invoice_facts
                            GROUP BY invoice_identity_key
                            HAVING count(*) > 1
                        ) d
                        """
                    )
                ).scalar_one()
            )
            evidence["duplicate_invoice_identity_count"] = duplicate_identity_count
            if duplicate_identity_count:
                failures.append(
                    f"duplicate_invoice_identity_count={duplicate_identity_count}"
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
    parser.add_argument("--result", required=True, help="Task 08 APPLY result JSON")
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
