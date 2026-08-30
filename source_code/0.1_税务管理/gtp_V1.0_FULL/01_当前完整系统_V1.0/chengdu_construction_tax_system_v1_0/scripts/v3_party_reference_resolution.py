#!/usr/bin/env python3
"""Resolve V3 Phase-A legacy party-reference conflicts without guessing.

Default mode is read-only. Write mode requires a human-reviewed manifest and
an exact ``--confirm-database`` match. The tool never fuzzy-matches, infers, or
lets UNKNOWN/A/B/C/D become new ExternalParty master rows. All writes are one
PostgreSQL transaction and are recorded in ``audit_logs``.

This closes Phase A reference conflicts; it is not the V3 Party Layer itself.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url

COLUMNS: tuple[tuple[str, str], ...] = (
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
ROLE_PLACEHOLDERS = {"A", "B", "C", "D"}
UNKNOWN_SENTINELS = {"UNKNOWN"}
ALLOWED_ACTIONS = {"MAP_TO_EXISTING", "ADD_EXTERNAL_MASTER"}
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class Decision:
    source_code: str
    action: str
    reason: str
    reviewed_by: str
    target_code: str | None = None
    external_party: dict[str, Any] | None = None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _database_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        raise SystemExit("DATABASE_URL is required")
    url = make_url(raw)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("V3 party-reference resolution is PostgreSQL-only")
    return raw


def _engine() -> Engine:
    return create_engine(_database_url(), future=True, pool_pre_ping=True)


def _classify(code: str, internal_codes: set[str], external_codes: set[str]) -> str:
    if not code:
        return "BLANK"
    if code.upper() in UNKNOWN_SENTINELS:
        return "UNKNOWN_SENTINEL"
    if code in ROLE_PLACEHOLDERS:
        return "ROLE_PLACEHOLDER"
    if code in internal_codes:
        return "RESOLVED_INTERNAL"
    if code in external_codes:
        return "RESOLVED_EXTERNAL"
    return "UNRESOLVED"


def _master_codes(conn: Connection) -> tuple[set[str], set[str]]:
    internal = {
        _clean(value)
        for value in conn.execute(text("SELECT code FROM entities")).scalars().all()
        if _clean(value)
    }
    external = {
        _clean(value)
        for value in conn.execute(text("SELECT code FROM external_parties")).scalars().all()
        if _clean(value)
    }
    return internal, external


def _code_locations(conn: Connection, code: str) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    for table, column in COLUMNS:
        row = conn.execute(
            text(
                f'''SELECT COUNT(*) AS row_count, MIN(id) AS first_id, MAX(id) AS last_id
                    FROM "{table}"
                    WHERE BTRIM(COALESCE("{column}", '')) = :code'''
            ),
            {"code": code},
        ).mappings().one()
        count = int(row["row_count"] or 0)
        if not count:
            continue
        example_ids = [
            int(value)
            for value in conn.execute(
                text(
                    f'''SELECT id FROM "{table}"
                        WHERE BTRIM(COALESCE("{column}", '')) = :code
                        ORDER BY id LIMIT 20'''
                ),
                {"code": code},
            ).scalars().all()
        ]
        locations.append(
            {
                "table": table,
                "column": column,
                "row_count": count,
                "first_id": int(row["first_id"]) if row["first_id"] is not None else None,
                "last_id": int(row["last_id"]) if row["last_id"] is not None else None,
                "example_ids": example_ids,
            }
        )
    return locations


def scan(conn: Connection) -> dict[str, Any]:
    """Inventory unresolved/sentinel codes; blank references are reported only."""
    internal_codes, external_codes = _master_codes(conn)
    distinct_codes: set[str] = set()
    blank_locations: list[dict[str, Any]] = []

    for table, column in COLUMNS:
        values = conn.execute(
            text(f'''SELECT DISTINCT BTRIM(COALESCE("{column}", '')) FROM "{table}"''')
        ).scalars().all()
        for value in values:
            code = _clean(value)
            if code:
                distinct_codes.add(code)
                continue
            count = int(
                conn.execute(
                    text(
                        f'''SELECT COUNT(*) FROM "{table}"
                            WHERE NULLIF(BTRIM(COALESCE("{column}", '')), '') IS NULL'''
                    )
                ).scalar_one()
                or 0
            )
            if count:
                blank_locations.append(
                    {"table": table, "column": column, "row_count": count}
                )

    issues: list[dict[str, Any]] = []
    resolved_count = 0
    for code in sorted(distinct_codes):
        classification = _classify(code, internal_codes, external_codes)
        if classification.startswith("RESOLVED_"):
            resolved_count += 1
            continue
        issues.append(
            {
                "code": code,
                "classification": classification,
                "length": len(code),
                "locations": _code_locations(conn, code),
            }
        )

    issue_counts: dict[str, int] = {}
    for item in issues:
        kind = str(item["classification"])
        issue_counts[kind] = issue_counts.get(kind, 0) + 1
    if blank_locations:
        issue_counts["BLANK_ROWS"] = sum(
            int(location["row_count"]) for location in blank_locations
        )

    return {
        "status": "PASS" if not issues else "FAIL",
        "database": str(conn.execute(text("SELECT current_database()")).scalar_one()),
        "resolved_distinct_code_count": resolved_count,
        "issue_distinct_code_count": len(issues),
        "issue_counts": issue_counts,
        "issues": issues,
        "blank_locations": blank_locations,
        "blank_policy": (
            "Blank references are evidence to review but do not block this resolver because "
            "some legacy columns intentionally use blank for no counterparty."
        ),
        "rule": "No automatic guessing. Every mutation requires a reviewed manifest decision.",
    }


def load_manifest(path: str | Path) -> list[Decision]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be an object")
    if payload.get("version") != MANIFEST_VERSION:
        raise ValueError(f"manifest version must be {MANIFEST_VERSION}")
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list) or not raw_decisions:
        raise ValueError("manifest.decisions must be a non-empty array")

    decisions: list[Decision] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_decisions, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"decision #{index} must be an object")
        source = _clean(raw.get("source_code"))
        action = _clean(raw.get("action")).upper()
        reason = _clean(raw.get("reason"))
        reviewed_by = _clean(raw.get("reviewed_by"))
        reviewed = raw.get("reviewed") is True
        target = _clean(raw.get("target_code")) or None
        external = raw.get("external_party")

        if not source:
            raise ValueError(f"decision #{index}: source_code is required")
        if source in seen:
            raise ValueError(f"duplicate source_code in manifest: {source}")
        seen.add(source)
        if action not in ALLOWED_ACTIONS:
            raise ValueError(
                f"decision {source}: action must be one of {sorted(ALLOWED_ACTIONS)}"
            )
        if not reviewed or not reviewed_by or not reason:
            raise ValueError(
                f"decision {source}: reviewed=true, reviewed_by and reason are required"
            )

        if action == "MAP_TO_EXISTING":
            if not target:
                raise ValueError(f"decision {source}: target_code is required")
            if target == source:
                raise ValueError(f"decision {source}: target_code cannot equal source_code")
            if external is not None:
                raise ValueError(
                    f"decision {source}: external_party is only valid for ADD_EXTERNAL_MASTER"
                )
        else:
            if source.upper() in UNKNOWN_SENTINELS or source in ROLE_PLACEHOLDERS:
                raise ValueError(
                    f"decision {source}: sentinel/role placeholder cannot become ExternalParty"
                )
            if target is not None:
                raise ValueError(
                    f"decision {source}: target_code is not valid for ADD_EXTERNAL_MASTER"
                )
            if not isinstance(external, dict):
                raise ValueError(f"decision {source}: external_party object is required")
            if not _clean(external.get("name")):
                raise ValueError(f"decision {source}: external_party.name is required")
            if not _clean(external.get("kind")):
                raise ValueError(f"decision {source}: external_party.kind is required")

        decisions.append(
            Decision(
                source_code=source,
                action=action,
                reason=reason,
                reviewed_by=reviewed_by,
                target_code=target,
                external_party=external if isinstance(external, dict) else None,
            )
        )
    return decisions


def _external_code_max_length(conn: Connection) -> int | None:
    value = conn.execute(
        text(
            """
            SELECT character_maximum_length
            FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name='external_parties'
              AND column_name='code'
            """
        )
    ).scalar_one_or_none()
    return int(value) if value is not None else None


def validate_decisions_against_database(
    conn: Connection,
    decisions: Iterable[Decision],
) -> list[str]:
    """Return all DB-dependent validation errors without mutating anything."""
    internal_codes, external_codes = _master_codes(conn)
    known = internal_codes | external_codes
    current_issues = {
        str(item["code"]): item for item in scan(conn)["issues"]
    }
    max_external_len = _external_code_max_length(conn)
    errors: list[str] = []

    for decision in decisions:
        source = decision.source_code
        if source not in current_issues:
            errors.append(f"{source}: source_code is not currently unresolved/sentinel")
            continue
        if decision.action == "MAP_TO_EXISTING":
            assert decision.target_code is not None
            if decision.target_code not in known:
                errors.append(
                    f"{source}: target_code {decision.target_code!r} does not exist in master data"
                )
        else:
            if source in known:
                errors.append(f"{source}: master row already exists")
            if max_external_len is not None and len(source) > max_external_len:
                errors.append(
                    f"{source}: length {len(source)} exceeds external_parties.code "
                    f"VARCHAR({max_external_len}); requires an explicit schema migration"
                )
    return errors


def _audit(
    conn: Connection,
    *,
    decision: Decision,
    affected_rows: int,
    target: str,
) -> None:
    message = json.dumps(
        {
            "source_code": decision.source_code,
            "action": decision.action,
            "target": target,
            "affected_rows": affected_rows,
            "reason": decision.reason,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    conn.execute(
        text(
            """
            INSERT INTO audit_logs
                (action, object_type, object_id, message, actor, ip, request_id)
            VALUES
                ('V3_PARTY_REFERENCE_RESOLUTION', 'PartyReference', :object_id,
                 :message, :actor, '', '')
            """
        ),
        {
            "object_id": decision.source_code[:50],
            "message": message,
            "actor": decision.reviewed_by[:80],
        },
    )


def _apply_one(conn: Connection, decision: Decision) -> dict[str, Any]:
    affected = 0
    target = decision.source_code
    if decision.action == "MAP_TO_EXISTING":
        assert decision.target_code is not None
        target = decision.target_code
        for table, column in COLUMNS:
            result = conn.execute(
                text(
                    f'''UPDATE "{table}"
                        SET "{column}" = :target
                        WHERE BTRIM(COALESCE("{column}", '')) = :source'''
                ),
                {"target": target, "source": decision.source_code},
            )
            affected += int(result.rowcount or 0)
    else:
        assert decision.external_party is not None
        external = decision.external_party
        result = conn.execute(
            text(
                """
                INSERT INTO external_parties
                    (code, name, short_name, kind, tax_id, active)
                VALUES
                    (:code, :name, :short_name, :kind, :tax_id, true)
                """
            ),
            {
                "code": decision.source_code,
                "name": _clean(external.get("name")),
                "short_name": _clean(external.get("short_name")),
                "kind": _clean(external.get("kind")),
                "tax_id": _clean(external.get("tax_id")) or None,
            },
        )
        affected = int(result.rowcount or 0)

    _audit(conn, decision=decision, affected_rows=affected, target=target)
    return {
        "source_code": decision.source_code,
        "action": decision.action,
        "target": target,
        "affected_rows": affected,
    }


def apply_manifest(
    engine: Engine,
    *,
    decisions: list[Decision],
    confirm_database: str,
) -> dict[str, Any]:
    with engine.begin() as conn:
        actual_db = str(conn.execute(text("SELECT current_database()")).scalar_one())
        if not confirm_database or confirm_database != actual_db:
            raise ValueError(
                f"--confirm-database must exactly match current database {actual_db!r}"
            )
        errors = validate_decisions_against_database(conn, decisions)
        if errors:
            raise ValueError("manifest validation failed: " + "; ".join(errors))

        applied = [_apply_one(conn, decision) for decision in decisions]
        after = scan(conn)
        if after["status"] != "PASS":
            remaining = [str(item["code"]) for item in after["issues"]]
            raise ValueError(
                "resolution is incomplete; transaction rolled back; remaining codes: "
                + ", ".join(remaining)
            )
        return {
            "status": "PASS",
            "database": actual_db,
            "applied": applied,
            "post_scan": after,
        }


def _write_json(path: str | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory or explicitly resolve Phase-A legacy party-reference conflicts."
    )
    parser.add_argument("--json", dest="json_path", help="write the report/result JSON")
    parser.add_argument("--manifest", help="human-reviewed resolution manifest JSON")
    parser.add_argument("--apply", action="store_true", help="apply the reviewed manifest")
    parser.add_argument(
        "--confirm-database",
        default="",
        help="required with --apply; must exactly equal PostgreSQL current_database()",
    )
    args = parser.parse_args()

    if args.apply and not args.manifest:
        parser.error("--apply requires --manifest")
    if args.apply and not args.confirm_database:
        parser.error("--apply requires --confirm-database")

    engine = _engine()
    try:
        if not args.apply:
            with engine.connect() as conn:
                report = scan(conn)
                if args.manifest:
                    decisions = load_manifest(args.manifest)
                    errors = validate_decisions_against_database(conn, decisions)
                    report["manifest_validation"] = {
                        "status": "PASS" if not errors else "FAIL",
                        "errors": errors,
                    }
            _write_json(args.json_path, report)
            if args.manifest and report.get("manifest_validation", {}).get("status") == "FAIL":
                return 2
            return 0 if report["status"] == "PASS" else 1

        decisions = load_manifest(args.manifest)
        result = apply_manifest(
            engine,
            decisions=decisions,
            confirm_database=args.confirm_database,
        )
        _write_json(args.json_path, result)
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        _write_json(args.json_path, {"status": "FAIL", "error": str(exc)})
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
