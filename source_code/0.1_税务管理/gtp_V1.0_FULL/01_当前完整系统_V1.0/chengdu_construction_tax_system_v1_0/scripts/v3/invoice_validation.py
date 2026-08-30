#!/usr/bin/env python3
"""Task 09 evidence-aware Invoice Fact validation runner.

PLAN mode is read-only. APPLY requires the exact saved plan and exact database
name confirmation. No invoice business values are edited: Task 09 only updates
``facts.validation_status`` according to deterministic stored evidence and
records an audit log.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.domain.invoice.validation import (  # noqa: E402
    InvoiceEvidenceSnapshot,
    RULESET_VERSION,
    evaluate_invoice_evidence,
)

PLAN_KIND = "V3_TASK09_INVOICE_VALIDATION_PLAN"
RESULT_KIND = "V3_TASK09_INVOICE_VALIDATION_RESULT"
EXPECTED_HEAD = "79_v3_legacy_invoice_pilot_bridge"
MAX_SELECTION = 1000


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task 09 validation is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    script_location = Path(cfg.get_main_option("script_location"))
    if not script_location.is_absolute():
        cfg.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def _db_heads(conn) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
        if row[0]
    }


def _current_database(conn) -> str:
    return str(conn.execute(text("SELECT current_database()" )).scalar_one())


def _require_expected_head(conn) -> None:
    db_heads = _db_heads(conn)
    disk_heads = _disk_heads()
    if db_heads != disk_heads:
        raise RuntimeError(
            f"Tax Alembic head mismatch: db={sorted(db_heads)} disk={sorted(disk_heads)}"
        )
    if disk_heads != {EXPECTED_HEAD}:
        raise RuntimeError(
            f"Task 09 requires unchanged head {EXPECTED_HEAD}; disk={sorted(disk_heads)}"
        )


def _snapshot_sql() -> str:
    return """
        WITH line_stats AS (
            SELECT invoice_fact_id,
                   count(*)::int AS line_count,
                   count(*) FILTER (
                       WHERE net_amount IS NULL OR vat_amount IS NULL
                   )::int AS incomplete_line_count,
                   sum(net_amount) AS line_net_sum,
                   sum(vat_amount) AS line_vat_sum
            FROM invoice_lines
            GROUP BY invoice_fact_id
        ),
        provenance_stats AS (
            SELECT fact_id,
                   count(*)::int AS provenance_count,
                   count(*) FILTER (WHERE document_id IS NOT NULL)::int
                       AS document_provenance_count
            FROM fact_provenance
            GROUP BY fact_id
        ),
        seller_tax AS (
            SELECT party_id, count(*)::int AS seller_tax_identifier_count
            FROM party_identifiers
            WHERE active
              AND identifier_type='TAX_REGISTRATION_ID'
              AND btrim(identifier_value) <> ''
            GROUP BY party_id
        )
        SELECT f.id AS fact_id,
               f.validation_status AS current_validation_status,
               i.invoice_identity_key,
               i.invoice_identity_version,
               i.invoice_number,
               i.invoice_code,
               i.invoice_date,
               i.invoice_status,
               i.seller_party_id,
               i.buyer_party_id,
               i.gross_amount,
               i.net_amount,
               i.vat_amount,
               i.currency,
               COALESCE(ls.line_count, 0) AS line_count,
               COALESCE(ls.incomplete_line_count, 0) AS incomplete_line_count,
               ls.line_net_sum,
               ls.line_vat_sum,
               COALESCE(ps.provenance_count, 0) AS provenance_count,
               COALESCE(ps.document_provenance_count, 0) AS document_provenance_count,
               COALESCE(st.seller_tax_identifier_count, 0) AS seller_tax_identifier_count,
               (
                   SELECT count(*)::int
                   FROM fact_relationships r
                   WHERE r.source_fact_id=f.id
                     AND r.relationship_type='REVERSAL_OF'
               ) AS reversal_relation_count,
               (
                   SELECT count(*)::int
                   FROM fact_relationships r
                   WHERE r.target_fact_id=f.id
                     AND r.relationship_type='VOID_RELATION'
               ) AS void_relation_count
        FROM facts f
        JOIN invoice_facts i ON i.fact_id=f.id
        LEFT JOIN line_stats ls ON ls.invoice_fact_id=f.id
        LEFT JOIN provenance_stats ps ON ps.fact_id=f.id
        LEFT JOIN seller_tax st ON st.party_id=i.seller_party_id
        WHERE f.fact_type='INVOICE' AND f.is_current
    """


def _snapshot(mapping: dict[str, Any]) -> InvoiceEvidenceSnapshot:
    raw_date = mapping.get("invoice_date")
    invoice_date = raw_date if isinstance(raw_date, date) else None
    return InvoiceEvidenceSnapshot(
        fact_id=int(mapping["fact_id"]),
        current_validation_status=str(mapping["current_validation_status"]),
        invoice_identity_key=str(mapping.get("invoice_identity_key") or ""),
        invoice_identity_version=str(mapping.get("invoice_identity_version") or ""),
        invoice_number=str(mapping.get("invoice_number") or ""),
        invoice_code=(str(mapping["invoice_code"]) if mapping.get("invoice_code") is not None else None),
        invoice_date=invoice_date,
        invoice_status=(str(mapping["invoice_status"]) if mapping.get("invoice_status") is not None else None),
        seller_party_id=(int(mapping["seller_party_id"]) if mapping.get("seller_party_id") is not None else None),
        buyer_party_id=(int(mapping["buyer_party_id"]) if mapping.get("buyer_party_id") is not None else None),
        gross_amount=(Decimal(mapping["gross_amount"]) if mapping.get("gross_amount") is not None else None),
        net_amount=(Decimal(mapping["net_amount"]) if mapping.get("net_amount") is not None else None),
        vat_amount=(Decimal(mapping["vat_amount"]) if mapping.get("vat_amount") is not None else None),
        currency=str(mapping.get("currency") or ""),
        line_count=int(mapping.get("line_count") or 0),
        incomplete_line_count=int(mapping.get("incomplete_line_count") or 0),
        line_net_sum=(Decimal(mapping["line_net_sum"]) if mapping.get("line_net_sum") is not None else None),
        line_vat_sum=(Decimal(mapping["line_vat_sum"]) if mapping.get("line_vat_sum") is not None else None),
        provenance_count=int(mapping.get("provenance_count") or 0),
        document_provenance_count=int(mapping.get("document_provenance_count") or 0),
        seller_tax_identifier_count=int(mapping.get("seller_tax_identifier_count") or 0),
        reversal_relation_count=int(mapping.get("reversal_relation_count") or 0),
        void_relation_count=int(mapping.get("void_relation_count") or 0),
    )


def _fetch_snapshots(
    conn,
    *,
    ids: Iterable[int] | None = None,
    all_review: bool = False,
) -> list[InvoiceEvidenceSnapshot]:
    sql = _snapshot_sql()
    params: dict[str, Any] = {}
    if ids is not None:
        selected = tuple(sorted({int(value) for value in ids}))
        if not selected:
            raise RuntimeError("invoice Fact selection is empty")
        if len(selected) > MAX_SELECTION:
            raise RuntimeError(f"Task 09 selection exceeds {MAX_SELECTION} Facts")
        sql += " AND f.id IN :ids"
        stmt = text(sql + " ORDER BY f.id").bindparams(bindparam("ids", expanding=True))
        params["ids"] = selected
        rows = [_snapshot(dict(row._mapping)) for row in conn.execute(stmt, params)]
        found = {row.fact_id for row in rows}
        missing = sorted(set(selected) - found)
        if missing:
            raise RuntimeError(f"selected current invoice Fact ids not found: {missing}")
        return rows

    if all_review:
        sql += " AND f.validation_status IN ('DRAFT','NEEDS_REVIEW')"
    else:
        raise RuntimeError("Task 09 selection mode missing")
    rows = [_snapshot(dict(row._mapping)) for row in conn.execute(text(sql + " ORDER BY f.id"))]
    if len(rows) > MAX_SELECTION:
        raise RuntimeError(
            f"Task 09 --all-review selected {len(rows)} Facts; use explicit --ids in chunks <= {MAX_SELECTION}"
        )
    return rows


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _canonical_plan(snapshots: list[InvoiceEvidenceSnapshot]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for snapshot in sorted(snapshots, key=lambda item: item.fact_id):
        decision = evaluate_invoice_evidence(snapshot)
        items.append(
            {
                "fact_id": snapshot.fact_id,
                "snapshot": _jsonable(asdict(snapshot)),
                "decision": decision.as_dict(),
            }
        )
    return {
        "version": 1,
        "ruleset_version": RULESET_VERSION,
        "selected_ids": [item["fact_id"] for item in items],
        "items": items,
    }


def _digest(plan: dict[str, Any]) -> str:
    raw = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_wrapper(conn, snapshots: list[InvoiceEvidenceSnapshot]) -> dict[str, Any]:
    if not snapshots:
        raise RuntimeError("no current DRAFT/NEEDS_REVIEW Invoice Facts selected")
    plan = _canonical_plan(snapshots)
    return {
        "kind": PLAN_KIND,
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": _current_database(conn),
        "alembic_head": EXPECTED_HEAD,
        "plan_digest": _digest(plan),
        "plan": plan,
    }


def _read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Task 09 plan must be a JSON object")
    return payload


def _write_json(path: str | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")


def _rebuild_saved_plan(conn, wrapper: dict[str, Any]) -> dict[str, Any]:
    plan = wrapper.get("plan")
    if not isinstance(plan, dict):
        raise RuntimeError("Task 09 plan payload missing")
    ids = [int(value) for value in plan.get("selected_ids", [])]
    snapshots = _fetch_snapshots(conn, ids=ids)
    return _build_wrapper(conn, snapshots)


def _apply(conn, wrapper: dict[str, Any]) -> dict[str, Any]:
    if wrapper.get("kind") != PLAN_KIND or int(wrapper.get("version", 0)) != 1:
        raise RuntimeError("unsupported Task 09 plan format")
    plan = wrapper.get("plan")
    if not isinstance(plan, dict) or plan.get("ruleset_version") != RULESET_VERSION:
        raise RuntimeError("Task 09 plan ruleset version mismatch")

    current = _rebuild_saved_plan(conn, wrapper)
    if current["plan_digest"] != wrapper.get("plan_digest") or current["plan"] != plan:
        raise RuntimeError("Task 09 plan is stale; evidence/status changed, regenerate the plan")

    changed = 0
    results: list[dict[str, Any]] = []
    for item in plan["items"]:
        fact_id = int(item["fact_id"])
        snapshot = item["snapshot"]
        decision = item["decision"]
        before = str(snapshot["current_validation_status"])
        after = str(decision["desired_status"])
        if after not in {"VALID", "INVALID", "NEEDS_REVIEW"}:
            raise RuntimeError(f"Fact {fact_id}: unsupported desired status {after!r}")
        if snapshot.get("invoice_identity_version") == "LEGACY_MIGRATION_V1" and after == "VALID":
            raise RuntimeError(f"Fact {fact_id}: legacy migration identity cannot be promoted to VALID")

        if before != after:
            result = conn.execute(
                text(
                    "UPDATE facts SET validation_status=:after "
                    "WHERE id=:fact_id AND validation_status=:before AND is_current"
                ),
                {"after": after, "fact_id": fact_id, "before": before},
            )
            if int(result.rowcount or 0) != 1:
                raise RuntimeError(f"Fact {fact_id}: concurrent validation status change detected")
            changed += 1

        finding_codes = [str(row["code"]) for row in decision.get("findings", [])]
        conn.execute(
            text(
                """
                INSERT INTO audit_logs(action, object_type, object_id, message, actor, ip, request_id)
                VALUES ('V3_INVOICE_VALIDATION','invoice_fact',:object_id,:message,'v3_validator','','')
                """
            ),
            {
                "object_id": str(fact_id),
                "message": json.dumps(
                    {
                        "ruleset_version": RULESET_VERSION,
                        "before": before,
                        "after": after,
                        "finding_codes": finding_codes,
                        "plan_digest": wrapper["plan_digest"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        )
        results.append(
            {
                "fact_id": fact_id,
                "before_status": before,
                "after_status": after,
                "finding_codes": finding_codes,
            }
        )

    return {
        "kind": RESULT_KIND,
        "version": 1,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "database": _current_database(conn),
        "alembic_head": EXPECTED_HEAD,
        "ruleset_version": RULESET_VERSION,
        "plan_digest": wrapper["plan_digest"],
        "selected_ids": [int(value) for value in plan["selected_ids"]],
        "selected_count": len(plan["selected_ids"]),
        "changed_status_count": changed,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--ids", help="comma-separated current Invoice Fact ids")
    selection.add_argument(
        "--all-review",
        action="store_true",
        help="select all current DRAFT/NEEDS_REVIEW Invoice Facts (max 1000)",
    )
    parser.add_argument("--plan", help="saved PLAN JSON; required for --apply")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    if not args.apply:
        if args.plan:
            raise SystemExit("--plan is only used with --apply")
        if not args.ids and not args.all_review:
            raise SystemExit("PLAN mode requires --ids or --all-review")
        with engine.connect() as conn:
            conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                _require_expected_head(conn)
                if args.ids:
                    ids = {int(value.strip()) for value in args.ids.split(",") if value.strip()}
                    snapshots = _fetch_snapshots(conn, ids=ids)
                else:
                    snapshots = _fetch_snapshots(conn, all_review=True)
                payload = _build_wrapper(conn, snapshots)
            finally:
                conn.rollback()
        engine.dispose()
        _write_json(args.json_path, payload)
        return 0

    if not args.plan:
        raise SystemExit("--apply requires --plan <saved-plan.json>")
    if not args.confirm_database:
        raise SystemExit("--apply requires --confirm-database <exact current_database()>")
    if args.ids or args.all_review:
        raise SystemExit("--apply uses the exact saved plan; do not pass selection arguments")

    wrapper = _read_json(args.plan)
    with engine.begin() as conn:
        _require_expected_head(conn)
        database = _current_database(conn)
        if database != args.confirm_database:
            raise RuntimeError(
                f"database confirmation mismatch: actual={database!r} confirmed={args.confirm_database!r}"
            )
        if wrapper.get("database") != database:
            raise RuntimeError(
                f"plan belongs to database {wrapper.get('database')!r}, not {database!r}"
            )
        result = _apply(conn, wrapper)
    engine.dispose()
    _write_json(args.json_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
