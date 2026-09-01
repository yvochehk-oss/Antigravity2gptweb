#!/usr/bin/env python3
"""Task 10 Input VAT Claim pilot.

Discovery and PLAN modes are read-only. APPLY creates only fail-closed legacy
claim candidates: ``LEGACY_ASSUMPTION + LOW + NEEDS_REVIEW``. Legacy invoice
``period`` is used only as a review assumption for ``claim_period``; it is never
silently treated as confirmed VAT evidence.
"""
from __future__ import annotations

import argparse
import calendar
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.domain.party.resolver import (  # noqa: E402
    PartyResolutionError,
    TaxProfileWindow,
    resolve_reporting_party,
)

EXPECTED_HEAD = "80_v3_input_vat_claims"
PLAN_KIND = "V3_TASK10_INPUT_VAT_PILOT_PLAN"
RESULT_KIND = "V3_TASK10_INPUT_VAT_PILOT_RESULT"
SOURCE_SYSTEM = "LEGACY_INVOICES"
MONEY_TOLERANCE = Decimal("0.01")
PERIOD_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task 10 Input VAT pilot is PostgreSQL-only")
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


def _require_head(conn) -> None:
    db_heads = _db_heads(conn)
    disk_heads = _disk_heads()
    if db_heads != disk_heads:
        raise RuntimeError(
            f"Tax Alembic head mismatch: db={sorted(db_heads)} disk={sorted(disk_heads)}"
        )
    if disk_heads != {EXPECTED_HEAD}:
        raise RuntimeError(
            f"Task 10 requires head {EXPECTED_HEAD}; disk={sorted(disk_heads)}"
        )


def _current_database(conn) -> str:
    return str(conn.execute(text("SELECT current_database()" )).scalar_one())


def _parse_period(value: str) -> date:
    raw = str(value or "").strip()
    match = PERIOD_RE.fullmatch(raw)
    if not match:
        raise ValueError("period must be YYYY-MM")
    return date(int(match.group(1)), int(match.group(2)), 1)


def _month_end(period_start: date) -> date:
    return date(
        period_start.year,
        period_start.month,
        calendar.monthrange(period_start.year, period_start.month)[1],
    )


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _load_profiles(conn) -> tuple[TaxProfileWindow, ...]:
    return tuple(
        TaxProfileWindow(
            party_id=int(row["party_id"]),
            tax_type=str(row["tax_type"]),
            reporting_party_id=int(row["reporting_party_id"]),
            effective_from=row["effective_from"],
            effective_to=row["effective_to"],
            reviewed=bool(row["reviewed"]),
        )
        for row in conn.execute(
            text(
                """
                SELECT party_id, tax_type, reporting_party_id,
                       effective_from, effective_to, reviewed
                FROM party_tax_profiles
                WHERE tax_type='VAT'
                ORDER BY party_id, effective_from, id
                """
            )
        ).mappings()
    )


def _stable_reporting_party(
    party_id: int,
    period_start: date,
    profiles: tuple[TaxProfileWindow, ...],
) -> int:
    start_reporting = resolve_reporting_party(
        party_id,
        period_start,
        profiles,
        tax_type="VAT",
        require_reviewed=True,
    )
    end_reporting = resolve_reporting_party(
        party_id,
        _month_end(period_start),
        profiles,
        tax_type="VAT",
        require_reviewed=True,
    )
    if start_reporting != end_reporting:
        raise PartyResolutionError(
            f"VAT reporting party changes inside {period_start:%Y-%m}: "
            f"{start_reporting}->{end_reporting}"
        )
    return start_reporting


def discover(conn) -> dict[str, Any]:
    _require_head(conn)
    rows = [
        dict(row)
        for row in conn.execute(
            text(
                """
                SELECT i.entity_code,
                       i.period,
                       count(*)::int AS legacy_candidate_count,
                       count(m.invoice_fact_id)::int AS mapped_candidate_count,
                       coalesce(sum(i.vat), 0) AS legacy_vat_total,
                       coalesce(sum(i.vat) FILTER (WHERE m.invoice_fact_id IS NOT NULL), 0)
                           AS mapped_vat_total
                FROM invoices i
                LEFT JOIN legacy_invoice_map m ON m.legacy_invoice_id=i.id
                WHERE lower(i.direction)='in'
                  AND i.deductible
                  AND i.vat <> 0
                  AND i.period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
                GROUP BY i.entity_code, i.period
                ORDER BY i.period, i.entity_code
                """
            )
        ).mappings()
    ]
    internal = {
        str(row["canonical_code"]): int(row["party_id"])
        for row in conn.execute(
            text(
                "SELECT canonical_code, party_id FROM internal_entities WHERE active"
            )
        ).mappings()
    }
    profiles = _load_profiles(conn)
    output: list[dict[str, Any]] = []
    for row in rows:
        entity_code = str(row["entity_code"])
        period = str(row["period"])
        party_id = internal.get(entity_code)
        blockers: list[str] = []
        reporting_party_id: int | None = None
        if party_id is None:
            blockers.append("INTERNAL_PARTY_NOT_FOUND")
        else:
            try:
                reporting_party_id = _stable_reporting_party(
                    party_id,
                    _parse_period(period),
                    profiles,
                )
            except PartyResolutionError as exc:
                blockers.append(f"REPORTING_PARTY_UNRESOLVED:{exc}")
        legacy_count = int(row["legacy_candidate_count"])
        mapped_count = int(row["mapped_candidate_count"])
        if mapped_count != legacy_count:
            blockers.append(
                f"LEGACY_MAP_INCOMPLETE:{mapped_count}/{legacy_count}"
            )
        output.append(
            {
                "entity_code": entity_code,
                "period": period,
                "entity_party_id": party_id,
                "reporting_party_id": reporting_party_id,
                "legacy_candidate_count": legacy_count,
                "mapped_candidate_count": mapped_count,
                "legacy_vat_total": str(_money(row["legacy_vat_total"])),
                "mapped_vat_total": str(_money(row["mapped_vat_total"])),
                "eligible": not blockers and legacy_count > 0,
                "blockers": blockers,
            }
        )
    return {
        "kind": "V3_TASK10_INPUT_VAT_DISCOVERY",
        "database": _current_database(conn),
        "alembic_head": EXPECTED_HEAD,
        "eligible_entity_months": [row for row in output if row["eligible"]],
        "all_entity_months": output,
    }


def _source_rows(conn, entity_code: str, period: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            text(
                """
                SELECT i.id AS legacy_invoice_id,
                       i.entity_code,
                       i.period,
                       i.vat AS legacy_vat,
                       m.invoice_fact_id,
                       m.migration_status,
                       f.is_current,
                       f.validation_status,
                       inf.buyer_party_id,
                       inf.vat_amount AS invoice_fact_vat
                FROM invoices i
                LEFT JOIN legacy_invoice_map m ON m.legacy_invoice_id=i.id
                LEFT JOIN facts f ON f.id=m.invoice_fact_id
                LEFT JOIN invoice_facts inf ON inf.fact_id=m.invoice_fact_id
                WHERE i.entity_code=:entity_code
                  AND i.period=:period
                  AND lower(i.direction)='in'
                  AND i.deductible
                  AND i.vat <> 0
                ORDER BY i.id
                """
            ),
            {"entity_code": entity_code, "period": period},
        ).mappings()
    ]


def _build_canonical_plan(conn, entity_code: str, period: str) -> dict[str, Any]:
    period_start = _parse_period(period)
    source_rows = _source_rows(conn, entity_code, period)
    if not source_rows:
        raise RuntimeError(f"no legacy deductible input VAT candidates for {entity_code}/{period}")

    entity_party_id = conn.execute(
        text(
            """
            SELECT party_id
            FROM internal_entities
            WHERE canonical_code=:code AND active
            """
        ),
        {"code": entity_code},
    ).scalar_one_or_none()
    if entity_party_id is None:
        raise RuntimeError(f"active internal Party not found for {entity_code}")
    entity_party_id = int(entity_party_id)

    profiles = _load_profiles(conn)
    try:
        reporting_party_id = _stable_reporting_party(
            entity_party_id,
            period_start,
            profiles,
        )
    except PartyResolutionError as exc:
        raise RuntimeError(str(exc)) from exc

    blockers: list[str] = []
    candidates: list[dict[str, Any]] = []
    seen_invoice_facts: set[int] = set()
    for row in source_rows:
        legacy_id = int(row["legacy_invoice_id"])
        invoice_fact_id = row.get("invoice_fact_id")
        row_blockers: list[str] = []
        if invoice_fact_id is None:
            row_blockers.append("LEGACY_INVOICE_NOT_MAPPED")
        else:
            invoice_fact_id = int(invoice_fact_id)
            if invoice_fact_id in seen_invoice_facts:
                row_blockers.append("DUPLICATE_INPUT_VIEW_FOR_SAME_FACT")
            seen_invoice_facts.add(invoice_fact_id)
            if row.get("is_current") is not True:
                row_blockers.append("INVOICE_FACT_NOT_CURRENT")
            if str(row.get("validation_status") or "") in {"INVALID", "SUPERSEDED"}:
                row_blockers.append("INVOICE_FACT_NOT_CLAIM_CANDIDATE")
            if row.get("buyer_party_id") is None or int(row["buyer_party_id"]) != entity_party_id:
                row_blockers.append("INVOICE_BUYER_ENTITY_MISMATCH")
            if row.get("invoice_fact_vat") is None or abs(
                _money(row["invoice_fact_vat"]) - _money(row["legacy_vat"])
            ) > MONEY_TOLERANCE:
                row_blockers.append("LEGACY_AND_FACT_VAT_MISMATCH")

        legacy_vat = _money(row["legacy_vat"])
        event_type = "CLAIM" if legacy_vat > 0 else "ADJUSTMENT"
        external_claim_id = f"LEGACY_INVOICE:{legacy_id}"
        existing = conn.execute(
            text(
                """
                SELECT id, invoice_fact_id, reporting_party_id, claim_period,
                       claim_amount, event_type, claim_status, evidence_type, confidence
                FROM input_vat_claims
                WHERE source_system=:source_system
                  AND external_claim_id=:external_claim_id
                """
            ),
            {
                "source_system": SOURCE_SYSTEM,
                "external_claim_id": external_claim_id,
            },
        ).mappings().one_or_none()
        if existing is not None:
            expected = {
                "invoice_fact_id": invoice_fact_id,
                "reporting_party_id": reporting_party_id,
                "claim_period": period_start,
                "claim_amount": legacy_vat,
                "event_type": event_type,
                "claim_status": "NEEDS_REVIEW",
                "evidence_type": "LEGACY_ASSUMPTION",
                "confidence": "LOW",
            }
            for key, value in expected.items():
                current = existing[key]
                if key == "claim_amount":
                    same = _money(current) == _money(value)
                else:
                    same = current == value
                if not same:
                    row_blockers.append(f"EXISTING_CLAIM_MISMATCH:{key}")

        if row_blockers:
            blockers.append(f"legacy_invoice_id={legacy_id}:" + ",".join(row_blockers))
        candidates.append(
            {
                "legacy_invoice_id": legacy_id,
                "invoice_fact_id": invoice_fact_id,
                "legacy_vat": str(legacy_vat),
                "claim_period": period_start.isoformat(),
                "reporting_party_id": reporting_party_id,
                "event_type": event_type,
                "claim_status": "NEEDS_REVIEW",
                "evidence_type": "LEGACY_ASSUMPTION",
                "confidence": "LOW",
                "source_system": SOURCE_SYSTEM,
                "external_claim_id": external_claim_id,
                "existing_claim_id": int(existing["id"]) if existing is not None else None,
                "blockers": row_blockers,
            }
        )

    legacy_total = sum((_money(row["legacy_vat"]) for row in source_rows), Decimal("0"))
    return {
        "version": 1,
        "entity_code": entity_code,
        "period": period,
        "entity_party_id": entity_party_id,
        "reporting_party_id": reporting_party_id,
        "legacy_candidate_count": len(source_rows),
        "legacy_candidate_vat_total": str(legacy_total.quantize(Decimal("0.01"))),
        "candidates": candidates,
        "blockers": blockers,
    }


def _digest(plan: dict[str, Any]) -> str:
    raw = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_plan(conn, entity_code: str, period: str) -> dict[str, Any]:
    _require_head(conn)
    canonical = _build_canonical_plan(conn, entity_code.strip().upper(), period)
    return {
        "kind": PLAN_KIND,
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": _current_database(conn),
        "alembic_head": EXPECTED_HEAD,
        "plan_digest": _digest(canonical),
        "plan": canonical,
    }


def _read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Task 10 plan must be a JSON object")
    return payload


def _write_json(path: str | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")


def apply_plan(conn, wrapper: dict[str, Any]) -> dict[str, Any]:
    if wrapper.get("kind") != PLAN_KIND or int(wrapper.get("version", 0)) != 1:
        raise RuntimeError("unsupported Task 10 plan format")
    plan = wrapper.get("plan")
    if not isinstance(plan, dict):
        raise RuntimeError("Task 10 plan payload missing")
    if plan.get("blockers"):
        raise RuntimeError(f"Task 10 plan has blockers: {plan['blockers']}")

    current = _build_canonical_plan(
        conn,
        str(plan["entity_code"]),
        str(plan["period"]),
    )
    if current.get("blockers"):
        raise RuntimeError(f"Task 10 evidence now has blockers: {current['blockers']}")
    if _digest(current) != wrapper.get("plan_digest") or current != plan:
        raise RuntimeError("Task 10 plan is stale; regenerate before APPLY")

    claim_ids: list[int] = []
    created_count = 0
    for candidate in plan["candidates"]:
        existing_id = candidate.get("existing_claim_id")
        if existing_id is not None:
            claim_ids.append(int(existing_id))
            continue
        row = conn.execute(
            text(
                """
                INSERT INTO input_vat_claims(
                    invoice_fact_id, reporting_party_id, claim_period, claim_amount,
                    event_type, claim_status, evidence_type, confidence,
                    source_system, external_claim_id, note
                ) VALUES (
                    :invoice_fact_id, :reporting_party_id, :claim_period, :claim_amount,
                    :event_type, 'NEEDS_REVIEW', 'LEGACY_ASSUMPTION', 'LOW',
                    :source_system, :external_claim_id, :note
                )
                RETURNING id
                """
            ),
            {
                "invoice_fact_id": int(candidate["invoice_fact_id"]),
                "reporting_party_id": int(candidate["reporting_party_id"]),
                "claim_period": date.fromisoformat(str(candidate["claim_period"])),
                "claim_amount": Decimal(str(candidate["legacy_vat"])),
                "event_type": str(candidate["event_type"]),
                "source_system": SOURCE_SYSTEM,
                "external_claim_id": str(candidate["external_claim_id"]),
                "note": "Task10 legacy deductible/period candidate; requires evidence review before CONFIRMED",
            },
        ).scalar_one()
        claim_ids.append(int(row))
        created_count += 1

    selected_external_ids = [str(row["external_claim_id"]) for row in plan["candidates"]]
    linked_rows = [
        dict(row)
        for row in conn.execute(
            text(
                """
                SELECT id, claim_amount, claim_status, evidence_type, confidence
                FROM input_vat_claims
                WHERE source_system=:source_system
                  AND external_claim_id = ANY(:external_ids)
                ORDER BY id
                """
            ),
            {"source_system": SOURCE_SYSTEM, "external_ids": selected_external_ids},
        ).mappings()
    ]
    review_total = sum(
        (_money(row["claim_amount"]) for row in linked_rows if row["claim_status"] == "NEEDS_REVIEW"),
        Decimal("0"),
    ).quantize(Decimal("0.01"))
    confirmed_linked_total = sum(
        (_money(row["claim_amount"]) for row in linked_rows if row["claim_status"] == "CONFIRMED"),
        Decimal("0"),
    ).quantize(Decimal("0.01"))
    legacy_total = Decimal(str(plan["legacy_candidate_vat_total"]))
    residual = (legacy_total - review_total - confirmed_linked_total).quantize(Decimal("0.01"))
    reporting_period_confirmed_total = _money(
        conn.execute(
            text(
                """
                SELECT coalesce(sum(claim_amount), 0)
                FROM input_vat_claims
                WHERE reporting_party_id=:reporting_party_id
                  AND claim_period=:claim_period
                  AND claim_status='CONFIRMED'
                """
            ),
            {
                "reporting_party_id": int(plan["reporting_party_id"]),
                "claim_period": _parse_period(str(plan["period"])),
            },
        ).scalar_one()
    )

    conn.execute(
        text(
            """
            INSERT INTO audit_logs(action, object_type, object_id, message, actor, ip, request_id)
            VALUES ('V3_INPUT_VAT_PILOT','input_vat_claims',:object_id,:message,'v3_migration','','')
            """
        ),
        {
            "object_id": f"{plan['entity_code']}:{plan['period']}",
            "message": json.dumps(
                {
                    "plan_digest": wrapper["plan_digest"],
                    "claim_ids": claim_ids,
                    "created_count": created_count,
                    "legacy_candidate_vat_total": str(legacy_total),
                    "review_candidate_vat_total": str(review_total),
                    "confirmed_linked_vat_total": str(confirmed_linked_total),
                    "explained_residual": str(residual),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    )

    return {
        "kind": RESULT_KIND,
        "version": 1,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "database": _current_database(conn),
        "alembic_head": EXPECTED_HEAD,
        "plan_digest": wrapper["plan_digest"],
        "entity_code": plan["entity_code"],
        "period": plan["period"],
        "entity_party_id": int(plan["entity_party_id"]),
        "reporting_party_id": int(plan["reporting_party_id"]),
        "legacy_candidate_count": int(plan["legacy_candidate_count"]),
        "claim_ids": claim_ids,
        "created_claim_count": created_count,
        "legacy_candidate_vat_total": str(legacy_total),
        "review_candidate_vat_total": str(review_total),
        "confirmed_linked_vat_total": str(confirmed_linked_total),
        "reporting_period_confirmed_input_vat": str(reporting_period_confirmed_total),
        "explained_residual": str(residual),
        "source_external_claim_ids": selected_external_ids,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--entity")
    parser.add_argument("--period")
    parser.add_argument("--plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)

    if args.apply:
        if not args.plan or not args.confirm_database:
            raise SystemExit("--apply requires --plan and --confirm-database")
        if args.discover or args.entity or args.period:
            raise SystemExit("--apply uses the exact saved plan; do not pass discovery/selection args")
        wrapper = _read_json(args.plan)
        with engine.begin() as conn:
            _require_head(conn)
            database = _current_database(conn)
            if database != args.confirm_database:
                raise RuntimeError(
                    f"database confirmation mismatch: actual={database!r} confirmed={args.confirm_database!r}"
                )
            if wrapper.get("database") != database:
                raise RuntimeError(
                    f"plan belongs to database {wrapper.get('database')!r}, not {database!r}"
                )
            payload = apply_plan(conn, wrapper)
        engine.dispose()
        _write_json(args.json_path, payload)
        return 0

    if args.plan:
        raise SystemExit("--plan is only used with --apply")
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            if args.discover:
                if args.entity or args.period:
                    raise SystemExit("--discover cannot be combined with --entity/--period")
                payload = discover(conn)
            else:
                if not args.entity or not args.period:
                    raise SystemExit("PLAN mode requires --entity <code> --period YYYY-MM")
                payload = build_plan(conn, args.entity, args.period)
        finally:
            conn.rollback()
    engine.dispose()
    _write_json(args.json_path, payload)
    return 0 if not payload.get("plan", {}).get("blockers") else 1


if __name__ == "__main__":
    raise SystemExit(main())
