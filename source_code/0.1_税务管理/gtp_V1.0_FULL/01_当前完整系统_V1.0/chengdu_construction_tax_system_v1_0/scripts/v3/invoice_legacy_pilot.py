#!/usr/bin/env python3
"""Task 08 small-batch legacy invoice migration runner.

PLAN mode is read-only.  The planner classifies the complete unmapped legacy
population first, then selects whole actions/clusters so a limit can never split
an internal IN/OUT pair.  APPLY requires the exact saved plan and an exact
database-name confirmation.  All writes run in one transaction.  No reader
cutover occurs here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.domain.invoice.legacy_migration import (  # noqa: E402
    MIGRATION_IDENTITY_VERSION,
    LegacyInvoiceSnapshot,
    PartyRef,
    PilotAction,
    PilotPlan,
    canonical_plan_payload,
    plan_digest,
    plan_pilot,
)
from app.v3_fact_models import Fact, InvoiceFact, LegacyInvoiceMap  # noqa: E402

PLAN_KIND = "V3_TASK08_LEGACY_INVOICE_PILOT_PLAN"
RESULT_KIND = "V3_TASK08_LEGACY_INVOICE_PILOT_RESULT"
EXPECTED_HEAD = "79_v3_legacy_invoice_pilot_bridge"
MAX_PILOT_ROWS = 500


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task 08 pilot is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    script_location = Path(cfg.get_main_option("script_location"))
    if not script_location.is_absolute():
        cfg.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def _current_database(conn) -> str:
    return str(conn.execute(text("SELECT current_database()" )).scalar_one())


def _db_heads(conn) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
        if row[0]
    }


def _require_expected_head(conn) -> None:
    db_heads = _db_heads(conn)
    disk_heads = _disk_heads()
    if db_heads != disk_heads:
        raise RuntimeError(
            f"Tax Alembic head mismatch: db={sorted(db_heads)} disk={sorted(disk_heads)}"
        )
    if disk_heads != {EXPECTED_HEAD}:
        raise RuntimeError(
            f"Task 08 requires head {EXPECTED_HEAD}; disk heads={sorted(disk_heads)}"
        )


def _snapshot(mapping: dict[str, Any]) -> LegacyInvoiceSnapshot:
    return LegacyInvoiceSnapshot(
        id=int(mapping["id"]),
        project_id=int(mapping["project_id"]),
        invoice_no=str(mapping.get("invoice_no") or ""),
        period=str(mapping.get("period") or ""),
        entity_code=str(mapping.get("entity_code") or ""),
        direction=str(mapping.get("direction") or ""),
        counterparty_code=str(mapping.get("counterparty_code") or ""),
        category=str(mapping.get("category") or ""),
        net=Decimal(mapping.get("net") or 0),
        vat=Decimal(mapping.get("vat") or 0),
        rate=Decimal(mapping.get("rate") or 0),
        deductible=bool(mapping.get("deductible")),
        note=str(mapping.get("note") or ""),
    )


def _invoice_select_sql() -> str:
    return """
        SELECT id, project_id, invoice_no, period, entity_code, direction,
               counterparty_code, category, net, vat, rate, deductible, note
        FROM invoices
    """


def _fetch_rows_by_ids(conn, ids: Iterable[int]) -> list[LegacyInvoiceSnapshot]:
    ids = tuple(sorted({int(value) for value in ids}))
    if not ids:
        return []
    stmt = text(_invoice_select_sql() + " WHERE id IN :ids ORDER BY id").bindparams(
        bindparam("ids", expanding=True)
    )
    rows = [_snapshot(dict(row._mapping)) for row in conn.execute(stmt, {"ids": ids})]
    found = {row.id for row in rows}
    missing = sorted(set(ids) - found)
    if missing:
        raise RuntimeError(f"legacy invoice ids not found: {missing}")
    return rows


def _fetch_all_unmapped_rows(conn) -> list[LegacyInvoiceSnapshot]:
    rows = conn.execute(
        text(
            _invoice_select_sql()
            + """
            WHERE NOT EXISTS (
                SELECT 1 FROM legacy_invoice_map m WHERE m.legacy_invoice_id=invoices.id
            )
            ORDER BY id
            """
        )
    )
    return [_snapshot(dict(row._mapping)) for row in rows]


def _legacy_map_count(conn) -> int:
    return int(conn.execute(text("SELECT count(*) FROM legacy_invoice_map")).scalar_one())


def _register_alias(
    aliases: dict[str, PartyRef],
    ambiguous: set[str],
    alias: object,
    party: PartyRef,
) -> None:
    from app.domain.invoice.legacy_migration import normalize_text

    key = normalize_text(alias)
    if not key:
        return
    existing = aliases.get(key)
    if existing is None and key not in ambiguous:
        aliases[key] = party
    elif existing is not None and existing.party_id != party.party_id:
        aliases.pop(key, None)
        ambiguous.add(key)


def _party_lookup(conn) -> tuple[dict[str, PartyRef], list[str]]:
    aliases: dict[str, PartyRef] = {}
    ambiguous: set[str] = set()
    parties = {
        int(row.id): PartyRef(int(row.id), str(row.code), str(row.party_type))
        for row in conn.execute(text("SELECT id, code, party_type FROM parties WHERE active"))
    }
    for party in parties.values():
        _register_alias(aliases, ambiguous, party.code, party)

    for row in conn.execute(
        text("SELECT party_id, canonical_code FROM internal_entities WHERE active")
    ):
        party = parties.get(int(row.party_id))
        if party:
            _register_alias(aliases, ambiguous, row.canonical_code, party)

    for row in conn.execute(
        text(
            "SELECT party_id, code FROM external_parties "
            "WHERE active AND party_id IS NOT NULL"
        )
    ):
        party = parties.get(int(row.party_id))
        if party:
            _register_alias(aliases, ambiguous, row.code, party)

    for row in conn.execute(
        text("SELECT party_id, identifier_value FROM party_identifiers WHERE active")
    ):
        party = parties.get(int(row.party_id))
        if party:
            _register_alias(aliases, ambiguous, row.identifier_value, party)

    return aliases, sorted(ambiguous)


def _existing_map_ids(conn, ids: Iterable[int]) -> list[int]:
    ids = tuple(sorted({int(value) for value in ids}))
    if not ids:
        return []
    stmt = text(
        "SELECT legacy_invoice_id FROM legacy_invoice_map "
        "WHERE legacy_invoice_id IN :ids ORDER BY legacy_invoice_id"
    ).bindparams(bindparam("ids", expanding=True))
    return [int(row[0]) for row in conn.execute(stmt, {"ids": ids})]


def _subset_plan(full_plan: PilotPlan, requested_ids: set[int]) -> PilotPlan:
    if not requested_ids:
        raise RuntimeError("pilot selection is empty")
    if len(requested_ids) > MAX_PILOT_ROWS:
        raise RuntimeError(
            f"explicit Task 08 pilot exceeds {MAX_PILOT_ROWS} row hard ceiling"
        )
    known = set(full_plan.selected_ids)
    missing = sorted(requested_ids - known)
    if missing:
        raise RuntimeError(
            f"requested ids are not currently-unmapped legacy invoices: {missing}"
        )

    selected_actions: list[PilotAction] = []
    selected_ids: set[int] = set()
    for action in full_plan.actions:
        action_ids = set(action.legacy_ids)
        overlap = action_ids & requested_ids
        if overlap and overlap != action_ids:
            raise RuntimeError(
                "explicit selection would split a deterministic/ambiguous invoice cluster: "
                f"requested={sorted(overlap)} full_cluster={sorted(action_ids)}"
            )
        if overlap:
            selected_actions.append(action)
            selected_ids.update(action_ids)
    if selected_ids != requested_ids:
        raise RuntimeError(
            f"selection coverage mismatch: requested={sorted(requested_ids)} "
            f"selected={sorted(selected_ids)}"
        )
    return PilotPlan(
        selected_ids=tuple(sorted(selected_ids)),
        actions=tuple(sorted(selected_actions, key=lambda item: item.legacy_ids)),
    )


def _limit_plan(full_plan: PilotPlan, limit: int) -> PilotPlan:
    if limit < 1 or limit > MAX_PILOT_ROWS:
        raise RuntimeError(f"--limit must be between 1 and {MAX_PILOT_ROWS}")
    selected_actions: list[PilotAction] = []
    selected_ids: set[int] = set()
    for action in full_plan.actions:
        action_ids = set(action.legacy_ids)
        if len(action_ids) > MAX_PILOT_ROWS:
            raise RuntimeError(
                "one legacy invoice cluster exceeds the Task 08 small-batch hard ceiling; "
                f"cluster_size={len(action_ids)} ids={sorted(action_ids)[:20]}"
            )
        if selected_actions and len(selected_ids) >= limit:
            break
        if selected_actions and len(selected_ids | action_ids) > MAX_PILOT_ROWS:
            break
        selected_actions.append(action)
        selected_ids.update(action_ids)
    if not selected_actions:
        raise RuntimeError("no unmapped legacy invoice rows remain")
    if len(selected_ids) > MAX_PILOT_ROWS:
        raise RuntimeError(
            f"Task 08 pilot selection exceeds {MAX_PILOT_ROWS} row hard ceiling"
        )
    return PilotPlan(
        selected_ids=tuple(sorted(selected_ids)),
        actions=tuple(selected_actions),
    )


def _canonical_for_selection(
    all_rows: list[LegacyInvoiceSnapshot],
    lookup: dict[str, PartyRef],
    *,
    explicit_ids: set[int] | None = None,
    limit: int | None = None,
) -> tuple[PilotPlan, list[LegacyInvoiceSnapshot]]:
    full_plan = plan_pilot(all_rows, lookup)
    if explicit_ids is not None:
        selected_plan = _subset_plan(full_plan, explicit_ids)
    elif limit is not None:
        selected_plan = _limit_plan(full_plan, limit)
    else:
        raise RuntimeError("selection mode missing")
    selected_set = set(selected_plan.selected_ids)
    selected_rows = [row for row in all_rows if row.id in selected_set]
    return selected_plan, selected_rows


def _build_plan_wrapper(
    conn,
    all_rows: list[LegacyInvoiceSnapshot],
    *,
    explicit_ids: set[int] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    lookup, ambiguous_aliases = _party_lookup(conn)
    selected_plan, selected_rows = _canonical_for_selection(
        all_rows,
        lookup,
        explicit_ids=explicit_ids,
        limit=limit,
    )
    canonical = canonical_plan_payload(selected_plan, selected_rows)
    return {
        "kind": PLAN_KIND,
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": _current_database(conn),
        "alembic_head": EXPECTED_HEAD,
        "plan_digest": plan_digest(canonical),
        "ambiguous_party_aliases": ambiguous_aliases,
        "plan": canonical,
    }


def _rebuild_saved_selection(conn, wrapper: dict[str, Any]) -> dict[str, Any]:
    plan = wrapper.get("plan")
    if not isinstance(plan, dict):
        raise RuntimeError("plan payload missing")
    requested_ids = {int(value) for value in plan.get("selected_ids", [])}
    all_rows = _fetch_all_unmapped_rows(conn)
    return _build_plan_wrapper(conn, all_rows, explicit_ids=requested_ids)


def _read_json(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _write_json(path: str | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")


def _representative(rows_by_id: dict[int, LegacyInvoiceSnapshot], ids: list[int]):
    return rows_by_id[min(ids)]


def _create_fact_for_action(
    session: Session,
    action: dict[str, Any],
    rows_by_id: dict[int, LegacyInvoiceSnapshot],
) -> int:
    ids = [int(value) for value in action["legacy_ids"]]
    source = _representative(rows_by_id, ids)
    identity_key = str(action["identity_key"])
    fact = Fact(
        fact_type="INVOICE",
        business_identity_key=f"INVOICE|{identity_key}",
        validation_status="NEEDS_REVIEW",
    )
    session.add(fact)
    session.flush()
    session.add(
        InvoiceFact(
            fact_id=fact.id,
            seller_party_id=int(action["seller_party_id"]),
            buyer_party_id=int(action["buyer_party_id"]),
            invoice_identity_key=identity_key,
            invoice_identity_version=MIGRATION_IDENTITY_VERSION,
            invoice_number=source.invoice_no,
            invoice_code=None,
            invoice_type=None,
            invoice_date=None,
            invoice_status=None,
            document_type="LEGACY_LEDGER_MIGRATION",
            gross_amount=source.net + source.vat,
            net_amount=source.net,
            vat_amount=source.vat,
            currency="CNY",
            invoice_medium=None,
            invoice_category=None,
        )
    )
    session.flush()
    return int(fact.id)


def _apply_plan(conn, wrapper: dict[str, Any]) -> dict[str, Any]:
    if wrapper.get("kind") != PLAN_KIND or int(wrapper.get("version", 0)) != 1:
        raise RuntimeError("unsupported Task 08 plan format")
    plan = wrapper.get("plan")
    if not isinstance(plan, dict):
        raise RuntimeError("plan payload missing")
    selected_ids = [int(value) for value in plan.get("selected_ids", [])]
    if not selected_ids:
        raise RuntimeError("pilot plan contains no selected ids")
    if len(selected_ids) > MAX_PILOT_ROWS:
        raise RuntimeError(
            f"refusing apply: Task 08 pilot exceeds {MAX_PILOT_ROWS} row hard ceiling"
        )

    # Task 08 is intentionally a single small pilot.  Refuse incremental runs
    # because a later row could be the missing perspective of an already-mapped
    # singleton and would require a separate reviewed reconciliation workflow.
    existing_total = _legacy_map_count(conn)
    if existing_total:
        raise RuntimeError(
            f"refusing Task 08 pilot: legacy_invoice_map already contains {existing_total} rows; "
            "do not run a second pilot batch without a reviewed reconciliation step"
        )

    existing = _existing_map_ids(conn, selected_ids)
    if existing:
        raise RuntimeError(f"selected legacy invoices already mapped: {existing}")

    bridge_stmt = text(
        "SELECT invoice_id, invoice_fact_id FROM real_cost_invoice_links "
        "WHERE invoice_id IN :ids AND invoice_fact_id IS NOT NULL"
    ).bindparams(bindparam("ids", expanding=True))
    bridged = conn.execute(bridge_stmt, {"ids": selected_ids}).all()
    if bridged:
        raise RuntimeError(
            "selected legacy invoices already have real-cost Fact bridges: "
            + repr([(int(row[0]), int(row[1])) for row in bridged])
        )

    current_wrapper = _rebuild_saved_selection(conn, wrapper)
    if current_wrapper["plan_digest"] != wrapper.get("plan_digest"):
        raise RuntimeError(
            "pilot plan is stale: source rows/Party resolution/group membership changed; "
            "regenerate the plan"
        )
    if current_wrapper["plan"] != plan:
        raise RuntimeError("pilot plan canonical payload mismatch")

    rows = _fetch_rows_by_ids(conn, selected_ids)
    rows_by_id = {row.id: row for row in rows}
    session = Session(bind=conn, expire_on_commit=False)
    applied_actions: list[dict[str, Any]] = []
    fact_ids: list[int] = []
    cost_links_bridged = 0
    try:
        for action in plan["actions"]:
            ids = [int(value) for value in action["legacy_ids"]]
            fact_id: int | None = None
            if action["action"] in {"MERGE_PAIR", "MIGRATE_SINGLE"}:
                fact_id = _create_fact_for_action(session, action, rows_by_id)
                fact_ids.append(fact_id)

            for legacy_id in ids:
                row = rows_by_id[legacy_id]
                session.add(
                    LegacyInvoiceMap(
                        legacy_invoice_id=legacy_id,
                        invoice_fact_id=fact_id,
                        legacy_direction=row.direction,
                        migration_status=str(action["migration_status"]),
                        reason=f"TASK08:{action['reason']}",
                    )
                )
            session.flush()

            if fact_id is not None:
                update_stmt = text(
                    """
                    UPDATE real_cost_invoice_links
                    SET invoice_fact_id=:fact_id
                    WHERE invoice_id IN :ids
                      AND invoice_fact_id IS NULL
                    """
                ).bindparams(bindparam("ids", expanding=True))
                result = session.execute(update_stmt, {"fact_id": fact_id, "ids": ids})
                cost_links_bridged += int(result.rowcount or 0)

            session.execute(
                text(
                    """
                    INSERT INTO audit_logs(action, object_type, object_id, message, actor, ip, request_id)
                    VALUES ('V3_INVOICE_PILOT','legacy_invoice',:object_id,:message,'v3_migration','','')
                    """
                ),
                {
                    "object_id": ",".join(str(value) for value in ids),
                    "message": json.dumps(
                        {
                            "action": action["action"],
                            "migration_status": action["migration_status"],
                            "reason": action["reason"],
                            "invoice_fact_id": fact_id,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            )
            applied_actions.append({**action, "invoice_fact_id": fact_id})
        session.flush()
    finally:
        session.close()

    return {
        "kind": RESULT_KIND,
        "version": 1,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "database": _current_database(conn),
        "alembic_head": EXPECTED_HEAD,
        "plan_digest": wrapper["plan_digest"],
        "selected_ids": selected_ids,
        "selected_count": len(selected_ids),
        "created_fact_ids": fact_ids,
        "created_fact_count": len(fact_ids),
        "real_cost_links_bridged": cost_links_bridged,
        "actions": applied_actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--ids", help="comma-separated explicit legacy invoice ids")
    selection.add_argument("--limit", type=int, help="approximate row limit; whole clusters are kept")
    parser.add_argument("--plan", help="saved PLAN JSON; required for --apply")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    if not args.apply:
        if args.plan:
            raise SystemExit("--plan is only used with --apply")
        if not args.ids and args.limit is None:
            raise SystemExit("PLAN mode requires explicit --ids or --limit")
        with engine.connect() as conn:
            conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                _require_expected_head(conn)
                if _legacy_map_count(conn):
                    raise RuntimeError(
                        "Task 08 pilot expects legacy_invoice_map to be empty; "
                        "a prior pilot/migration already exists"
                    )
                all_rows = _fetch_all_unmapped_rows(conn)
                if not all_rows:
                    raise RuntimeError("no unmapped legacy invoice rows remain")
                if args.ids:
                    explicit_ids = {
                        int(value.strip()) for value in args.ids.split(",") if value.strip()
                    }
                    payload = _build_plan_wrapper(
                        conn, all_rows, explicit_ids=explicit_ids
                    )
                else:
                    payload = _build_plan_wrapper(
                        conn, all_rows, limit=int(args.limit)
                    )
            finally:
                conn.rollback()
        engine.dispose()
        _write_json(args.json_path, payload)
        return 0

    if not args.plan:
        raise SystemExit("--apply requires --plan <saved-plan.json>")
    if not args.confirm_database:
        raise SystemExit("--apply requires --confirm-database <exact current_database()>")
    if args.ids or args.limit is not None:
        raise SystemExit("--apply uses the exact saved plan; do not pass --ids/--limit")

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
        result = _apply_plan(conn, wrapper)
    engine.dispose()
    _write_json(args.json_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
