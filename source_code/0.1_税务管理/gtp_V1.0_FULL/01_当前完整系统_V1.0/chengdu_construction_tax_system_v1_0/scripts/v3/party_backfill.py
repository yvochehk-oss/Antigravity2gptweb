#!/usr/bin/env python3
"""Task 06: idempotent Party backfill with conflict-first review.

The script is PostgreSQL-only and defaults to dry-run. It never merges Party
identity by fuzzy name or tax id. Exact legacy codes are the only automatic
identity bridge; every ambiguity is written to ``party_migration_conflicts`` in
apply mode and remains a Gate S1 review item.

Safe rows may be backfilled while unrelated conflicts remain. Parent/reporting
cycles block Gate S1 but do not prevent otherwise unambiguous Party identities
from being created.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url

DETECTOR = "TASK06_BACKFILL"
DEFAULT_PROFILE_FROM = date(1900, 1, 1)
DEFAULT_RULE_VERSION = "v3-task06-backfill-1"

LEGACY_REFERENCE_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("contracts", "buyer_code", None),
    ("contracts", "seller_code", None),
    ("invoices", "entity_code", "NULLIF(period, '')"),
    ("invoices", "counterparty_code", "NULLIF(period, '')"),
    (
        "cashflows",
        "entity_code",
        "COALESCE(NULLIF(transaction_date, ''), NULLIF(period, ''))",
    ),
    (
        "cashflows",
        "counterparty_code",
        "COALESCE(NULLIF(transaction_date, ''), NULLIF(period, ''))",
    ),
    ("fulfillment", "counterparty_code", None),
    ("real_costs", "entity_code", "NULLIF(period, '')"),
    ("real_costs", "counterparty_code", "NULLIF(period, '')"),
)


@dataclass(frozen=True)
class LegacyParty:
    source_table: str
    source_id: int
    code: str
    name: str
    short_name: str
    tax_id: str | None
    party_type: str
    active: bool
    business_role: str | None = None
    legal_entity: bool | None = None
    parent_code: str | None = None
    kind: str | None = None
    industry: str | None = None
    party_id: int | None = None


@dataclass(frozen=True)
class ExistingParty:
    party_id: int
    code: str
    name: str
    party_type: str
    active: bool


@dataclass(frozen=True)
class TaxProfileOverride:
    party_code: str
    tax_type: str
    reporting_party_code: str
    effective_from: date
    effective_to: date | None
    rule_version: str
    source: str
    reviewed: bool


@dataclass(frozen=True)
class ConflictRecord:
    conflict_type: str
    subject_code: str | None
    related_code: str | None
    legacy_source: str
    details: dict[str, Any]
    blocking: bool
    blocks_party_mapping: bool = False

    @property
    def conflict_key(self) -> str:
        payload = {
            "type": self.conflict_type,
            "subject": self.subject_code or "",
            "related": self.related_code or "",
            "source": self.legacy_source,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _optional(value: Any) -> str | None:
    cleaned = _clean(value)
    return cleaned or None


def _normal_name(value: str) -> str:
    return "".join(value.split()).casefold()


def _database_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        raise SystemExit("DATABASE_URL is required")
    if make_url(raw).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task 06 Party backfill is PostgreSQL-only")
    return raw


def _engine() -> Engine:
    return create_engine(_database_url(), future=True, pool_pre_ping=True)


def _parse_date(value: Any, *, field: str) -> date:
    raw = _clean(value)
    if not raw:
        raise ValueError(f"{field} is required")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD") from exc


def load_tax_profile_overrides(path: str | Path | None) -> dict[tuple[str, str], TaxProfileOverride]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("tax profile manifest must contain profiles[]")

    result: dict[tuple[str, str], TaxProfileOverride] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"profile #{index} must be an object")
        party_code = _clean(row.get("party_code"))
        tax_type = _clean(row.get("tax_type")).upper()
        reporting_code = _clean(row.get("reporting_party_code"))
        if tax_type != "VAT":
            raise ValueError(f"profile #{index}: Task 06 currently seeds VAT profiles only")
        if not party_code or not tax_type or not reporting_code:
            raise ValueError(
                f"profile #{index}: party_code, tax_type and reporting_party_code are required"
            )
        effective_from = _parse_date(row.get("effective_from"), field="effective_from")
        effective_to_raw = _optional(row.get("effective_to"))
        effective_to = (
            _parse_date(effective_to_raw, field="effective_to")
            if effective_to_raw
            else None
        )
        if effective_to is not None and effective_to < effective_from:
            raise ValueError(f"profile {party_code}/{tax_type}: invalid effective range")
        key = (party_code, tax_type)
        if key in result:
            raise ValueError(f"duplicate tax profile override for {party_code}/{tax_type}")
        result[key] = TaxProfileOverride(
            party_code=party_code,
            tax_type=tax_type,
            reporting_party_code=reporting_code,
            effective_from=effective_from,
            effective_to=effective_to,
            rule_version=_clean(row.get("rule_version")) or DEFAULT_RULE_VERSION,
            source=_clean(row.get("source")) or "MANUAL_REVIEW",
            reviewed=row.get("reviewed") is True,
        )
    return result


def cycle_nodes(parent_by_code: Mapping[str, str | None]) -> set[str]:
    """Return all nodes participating in at least one directed cycle."""
    cyclic: set[str] = set()
    for start in parent_by_code:
        order: list[str] = []
        position: dict[str, int] = {}
        current: str | None = start
        while current is not None and current in parent_by_code:
            if current in position:
                cyclic.update(order[position[current] :])
                break
            position[current] = len(order)
            order.append(current)
            current = parent_by_code.get(current)
    return cyclic


def detect_static_conflicts(
    internal: Iterable[LegacyParty],
    external: Iterable[LegacyParty],
    existing_by_code: Mapping[str, ExistingParty],
    existing_by_id: Mapping[int, ExistingParty],
    overrides: Mapping[tuple[str, str], TaxProfileOverride],
) -> list[ConflictRecord]:
    internals = tuple(internal)
    externals = tuple(external)
    all_rows = internals + externals
    conflicts: list[ConflictRecord] = []

    internal_codes = {row.code for row in internals}
    external_codes = {row.code for row in externals}

    for code in sorted(internal_codes & external_codes):
        conflicts.append(
            ConflictRecord(
                "CODE_COLLISION",
                code,
                code,
                "entities+external_parties",
                {"reason": "same code exists in internal and external legacy masters"},
                True,
                True,
            )
        )

    for row in all_rows:
        existing = existing_by_code.get(row.code)
        if existing is not None:
            if existing.party_type != row.party_type:
                conflicts.append(
                    ConflictRecord(
                        "EXISTING_PARTY_TYPE_MISMATCH",
                        row.code,
                        None,
                        row.source_table,
                        {
                            "legacy_party_type": row.party_type,
                            "existing_party_type": existing.party_type,
                        },
                        True,
                        True,
                    )
                )
            elif _normal_name(existing.name) != _normal_name(row.name):
                conflicts.append(
                    ConflictRecord(
                        "NAME_CHANGED",
                        row.code,
                        None,
                        row.source_table,
                        {"legacy_name": row.name, "party_name": existing.name},
                        True,
                        True,
                    )
                )

        if not row.tax_id:
            conflicts.append(
                ConflictRecord(
                    "EMPTY_TAX_ID",
                    row.code,
                    None,
                    row.source_table,
                    {"party_type": row.party_type},
                    False,
                )
            )
        if not row.active:
            conflicts.append(
                ConflictRecord(
                    "INACTIVE_ENTITY",
                    row.code,
                    None,
                    row.source_table,
                    {"party_type": row.party_type},
                    False,
                )
            )

        if row.source_table == "external_parties" and row.party_id is not None:
            bridged = existing_by_id.get(row.party_id)
            if (
                bridged is None
                or bridged.code != row.code
                or bridged.party_type != "external"
            ):
                conflicts.append(
                    ConflictRecord(
                        "EXTERNAL_BRIDGE_MISMATCH",
                        row.code,
                        bridged.code if bridged else None,
                        "external_parties.party_id",
                        {
                            "legacy_party_id": row.party_id,
                            "resolved_party": (
                                {
                                    "id": bridged.party_id,
                                    "code": bridged.code,
                                    "party_type": bridged.party_type,
                                }
                                if bridged
                                else None
                            ),
                        },
                        True,
                        True,
                    )
                )

    by_tax_id: dict[str, list[LegacyParty]] = {}
    for row in all_rows:
        if row.tax_id:
            by_tax_id.setdefault(row.tax_id, []).append(row)
    for tax_id, rows in sorted(by_tax_id.items()):
        codes = sorted({row.code for row in rows})
        if len(codes) > 1:
            conflicts.append(
                ConflictRecord(
                    "SHARED_TAX_ID",
                    codes[0],
                    codes[1],
                    "legacy_master",
                    {"tax_id": tax_id, "party_codes": codes},
                    False,
                )
            )

    by_name: dict[str, list[LegacyParty]] = {}
    for row in all_rows:
        by_name.setdefault(_normal_name(row.name), []).append(row)
    for rows in by_name.values():
        tax_ids = sorted({row.tax_id for row in rows if row.tax_id})
        codes = sorted({row.code for row in rows})
        if len(codes) > 1 and len(tax_ids) > 1:
            conflicts.append(
                ConflictRecord(
                    "NAME_MULTIPLE_TAX_IDS",
                    codes[0],
                    codes[1],
                    "legacy_master",
                    {
                        "name": rows[0].name,
                        "party_codes": codes,
                        "tax_ids": tax_ids,
                    },
                    True,
                    True,
                )
            )

    parent_by_code = {row.code: row.parent_code for row in internals}
    for row in internals:
        if row.parent_code and row.parent_code not in internal_codes:
            conflicts.append(
                ConflictRecord(
                    "PARENT_TARGET_MISSING",
                    row.code,
                    row.parent_code,
                    "entities.parent_entity_code",
                    {},
                    True,
                    False,
                )
            )
    for code in sorted(cycle_nodes(parent_by_code)):
        conflicts.append(
            ConflictRecord(
                "PARENT_CYCLE",
                code,
                parent_by_code.get(code),
                "entities.parent_entity_code",
                {},
                True,
                False,
            )
        )

    report_map: dict[str, str | None] = {}
    for override in overrides.values():
        if override.party_code not in internal_codes:
            conflicts.append(
                ConflictRecord(
                    "TAX_PROFILE_PARTY_UNKNOWN",
                    override.party_code,
                    override.reporting_party_code,
                    "tax_profile_manifest",
                    {"tax_type": override.tax_type},
                    True,
                    False,
                )
            )
            continue
        if override.reporting_party_code not in internal_codes:
            conflicts.append(
                ConflictRecord(
                    "TAX_PROFILE_REPORTING_PARTY_UNKNOWN",
                    override.party_code,
                    override.reporting_party_code,
                    "tax_profile_manifest",
                    {"tax_type": override.tax_type},
                    True,
                    False,
                )
            )
            continue
        if (
            override.reporting_party_code != override.party_code
            and not override.reviewed
        ):
            conflicts.append(
                ConflictRecord(
                    "TAX_PROFILE_REVIEW_REQUIRED",
                    override.party_code,
                    override.reporting_party_code,
                    "tax_profile_manifest",
                    {
                        "tax_type": override.tax_type,
                        "effective_from": override.effective_from.isoformat(),
                        "effective_to": (
                            override.effective_to.isoformat()
                            if override.effective_to
                            else None
                        ),
                    },
                    True,
                    False,
                )
            )
        if override.tax_type == "VAT":
            report_map[override.party_code] = override.reporting_party_code

    for code in internal_codes:
        report_map.setdefault(code, code)
    for code in sorted(cycle_nodes(report_map)):
        target = report_map.get(code)
        if target != code:
            conflicts.append(
                ConflictRecord(
                    "REPORTING_PARTY_CYCLE",
                    code,
                    target,
                    "tax_profile_manifest",
                    {"tax_type": "VAT"},
                    True,
                    False,
                )
            )

    unique: dict[str, ConflictRecord] = {}
    for conflict in conflicts:
        unique[conflict.conflict_key] = conflict
    return list(unique.values())


def _load_sources(
    conn: Connection,
) -> tuple[
    tuple[LegacyParty, ...],
    tuple[LegacyParty, ...],
    dict[str, ExistingParty],
    dict[int, ExistingParty],
]:
    internal = tuple(
        LegacyParty(
            source_table="entities",
            source_id=int(row["id"]),
            code=_clean(row["code"]),
            name=_clean(row["name"]),
            short_name=_clean(row["short_name"]),
            tax_id=_optional(row["tax_id"]),
            party_type="internal",
            active=bool(row["active"]),
            business_role=_optional(row["business_role"]),
            legal_entity=bool(row["legal_entity"]),
            parent_code=_optional(row["parent_entity_code"]),
        )
        for row in conn.execute(
            text(
                """
                SELECT id, code, name, short_name, tax_id, business_role,
                       legal_entity, parent_entity_code, active
                FROM entities
                ORDER BY code
                """
            )
        ).mappings()
    )

    external = tuple(
        LegacyParty(
            source_table="external_parties",
            source_id=int(row["id"]),
            code=_clean(row["code"]),
            name=_clean(row["name"]),
            short_name=_clean(row["short_name"]),
            tax_id=_optional(row["tax_id"]),
            party_type="external",
            active=bool(row["active"]),
            kind=_optional(row["kind"]),
            industry=_optional(row["industry"]),
            party_id=int(row["party_id"]) if row["party_id"] is not None else None,
        )
        for row in conn.execute(
            text(
                """
                SELECT id, code, name, short_name, tax_id, kind, industry,
                       active, party_id
                FROM external_parties
                ORDER BY code
                """
            )
        ).mappings()
    )

    existing_rows = [
        ExistingParty(
            party_id=int(row["id"]),
            code=_clean(row["code"]),
            name=_clean(row["name"]),
            party_type=_clean(row["party_type"]),
            active=bool(row["active"]),
        )
        for row in conn.execute(
            text("SELECT id, code, name, party_type, active FROM parties ORDER BY code")
        ).mappings()
    ]
    return (
        internal,
        external,
        {row.code: row for row in existing_rows},
        {row.party_id: row for row in existing_rows},
    )


def _usage_context(conn: Connection, code: str | None) -> dict[str, Any]:
    if not code:
        return {"code": None, "locations": []}
    locations: list[dict[str, Any]] = []
    for table, column, time_expr in LEGACY_REFERENCE_COLUMNS:
        time_sql = time_expr or "NULL::text"
        row = conn.execute(
            text(
                f"""
                SELECT COUNT(*) AS row_count,
                       MIN(id) AS first_id,
                       MAX(id) AS last_id,
                       MIN({time_sql}) AS earliest_time,
                       MAX({time_sql}) AS latest_time
                FROM "{table}"
                WHERE BTRIM(COALESCE("{column}", '')) = :code
                """
            ),
            {"code": code},
        ).mappings().one()
        count = int(row["row_count"] or 0)
        if count:
            locations.append(
                {
                    "table": table,
                    "column": column,
                    "row_count": count,
                    "first_id": int(row["first_id"]) if row["first_id"] is not None else None,
                    "last_id": int(row["last_id"]) if row["last_id"] is not None else None,
                    "earliest_time": _optional(row["earliest_time"]),
                    "latest_time": _optional(row["latest_time"]),
                }
            )
    return {"code": code, "locations": locations}


def _conflict_payload(conn: Connection, conflict: ConflictRecord) -> dict[str, Any]:
    return {
        "conflict_key": conflict.conflict_key,
        "conflict_type": conflict.conflict_type,
        "subject_code": conflict.subject_code,
        "related_code": conflict.related_code,
        "legacy_source": conflict.legacy_source,
        "blocking": conflict.blocking,
        "blocks_party_mapping": conflict.blocks_party_mapping,
        "details": conflict.details,
        "context": {
            "subject": _usage_context(conn, conflict.subject_code),
            "related": _usage_context(conn, conflict.related_code),
        },
    }


def build_plan(
    conn: Connection,
    overrides: Mapping[tuple[str, str], TaxProfileOverride],
) -> tuple[dict[str, Any], tuple[LegacyParty, ...], tuple[LegacyParty, ...]]:
    internal, external, existing_by_code, existing_by_id = _load_sources(conn)
    conflicts = detect_static_conflicts(
        internal,
        external,
        existing_by_code,
        existing_by_id,
        overrides,
    )
    payloads = [_conflict_payload(conn, item) for item in conflicts]
    blocked_codes = {
        code
        for item in conflicts
        if item.blocks_party_mapping
        for code in (item.subject_code, item.related_code)
        if code
    }
    blocking_count = sum(1 for item in conflicts if item.blocking)
    review_count = len(conflicts) - blocking_count
    plan = {
        "status": "FAIL" if blocking_count else ("WARNING" if review_count else "PASS"),
        "database": str(conn.execute(text("SELECT current_database()")).scalar_one()),
        "source_counts": {
            "internal_total": len(internal),
            "internal_active": sum(1 for row in internal if row.active),
            "internal_inactive": sum(1 for row in internal if not row.active),
            "external_total": len(external),
        },
        "existing_party_count": len(existing_by_code),
        "blocking_conflict_count": blocking_count,
        "review_conflict_count": review_count,
        "blocked_party_codes": sorted(blocked_codes),
        "safe_internal_codes": sorted(row.code for row in internal if row.code not in blocked_codes),
        "safe_external_codes": sorted(row.code for row in external if row.code not in blocked_codes),
        "conflicts": payloads,
        "rule": "Exact code only; no fuzzy/tax-id/name identity merge.",
    }
    return plan, internal, external


def _upsert_conflicts(conn: Connection, conflicts: Iterable[dict[str, Any]]) -> None:
    conn.execute(
        text(
            """
            UPDATE party_migration_conflicts
            SET status='CLEARED', resolved_at=CURRENT_TIMESTAMP
            WHERE detector=:detector AND status='OPEN'
            """
        ),
        {"detector": DETECTOR},
    )
    statement = text(
        """
        INSERT INTO party_migration_conflicts
            (conflict_key, conflict_type, subject_code, related_code,
             legacy_source, detector, details, context, status)
        VALUES
            (:conflict_key, :conflict_type, :subject_code, :related_code,
             :legacy_source, :detector, CAST(:details AS jsonb),
             CAST(:context AS jsonb), 'OPEN')
        ON CONFLICT (conflict_key) DO UPDATE SET
            conflict_type=EXCLUDED.conflict_type,
            subject_code=EXCLUDED.subject_code,
            related_code=EXCLUDED.related_code,
            legacy_source=EXCLUDED.legacy_source,
            detector=EXCLUDED.detector,
            details=EXCLUDED.details,
            context=EXCLUDED.context,
            status='OPEN',
            resolved_at=NULL
        """
    )
    for item in conflicts:
        details = dict(item["details"])
        details["blocking"] = bool(item["blocking"])
        details["blocks_party_mapping"] = bool(item["blocks_party_mapping"])
        conn.execute(
            statement,
            {
                "conflict_key": item["conflict_key"],
                "conflict_type": item["conflict_type"],
                "subject_code": item["subject_code"],
                "related_code": item["related_code"],
                "legacy_source": item["legacy_source"],
                "detector": DETECTOR,
                "details": json.dumps(details, ensure_ascii=False, sort_keys=True),
                "context": json.dumps(item["context"], ensure_ascii=False, sort_keys=True),
            },
        )


def _ensure_party(conn: Connection, row: LegacyParty) -> int:
    existing = conn.execute(
        text("SELECT id, name, party_type FROM parties WHERE code=:code"),
        {"code": row.code},
    ).mappings().one_or_none()
    if existing is not None:
        if (
            _clean(existing["party_type"]) != row.party_type
            or _normal_name(_clean(existing["name"])) != _normal_name(row.name)
        ):
            raise ValueError(f"party {row.code}: existing master no longer matches reviewed plan")
        return int(existing["id"])

    return int(
        conn.execute(
            text(
                """
                INSERT INTO parties (code, name, short_name, party_type, active)
                VALUES (:code, :name, :short_name, :party_type, :active)
                RETURNING id
                """
            ),
            {
                "code": row.code,
                "name": row.name,
                "short_name": row.short_name,
                "party_type": row.party_type,
                "active": row.active,
            },
        ).scalar_one()
    )


def _ensure_identifier(
    conn: Connection,
    *,
    party_id: int,
    identifier_type: str,
    identifier_value: str | None,
    source_system: str,
) -> None:
    if not identifier_value:
        return
    conn.execute(
        text(
            """
            INSERT INTO party_identifiers
                (party_id, identifier_type, identifier_value, source_system, active)
            VALUES
                (:party_id, :identifier_type, :identifier_value, :source_system, true)
            ON CONFLICT (party_id, identifier_type, identifier_value) DO NOTHING
            """
        ),
        {
            "party_id": party_id,
            "identifier_type": identifier_type,
            "identifier_value": identifier_value,
            "source_system": source_system,
        },
    )


def _ensure_internal_entity(conn: Connection, row: LegacyParty, party_id: int) -> None:
    existing = conn.execute(
        text(
            """
            SELECT party_id, business_role, legal_entity
            FROM internal_entities
            WHERE canonical_code=:code
            """
        ),
        {"code": row.code},
    ).mappings().one_or_none()
    if existing is None:
        conn.execute(
            text(
                """
                INSERT INTO internal_entities
                    (party_id, canonical_code, business_role, legal_entity, active)
                VALUES
                    (:party_id, :code, :business_role, :legal_entity, :active)
                """
            ),
            {
                "party_id": party_id,
                "code": row.code,
                "business_role": row.business_role or "",
                "legal_entity": bool(row.legal_entity),
                "active": row.active,
            },
        )
        return
    if int(existing["party_id"]) != party_id:
        raise ValueError(f"internal entity {row.code}: canonical_code already maps elsewhere")


def _ensure_external_bridge(conn: Connection, row: LegacyParty, party_id: int) -> None:
    current = conn.execute(
        text("SELECT party_id FROM external_parties WHERE id=:id"),
        {"id": row.source_id},
    ).scalar_one()
    if current is None:
        conn.execute(
            text(
                """
                UPDATE external_parties
                SET party_id=:party_id
                WHERE id=:id AND party_id IS NULL
                """
            ),
            {"party_id": party_id, "id": row.source_id},
        )
    elif int(current) != party_id:
        raise ValueError(f"external party {row.code}: bridge changed since plan")


def _set_parent_links(
    conn: Connection,
    internal: Iterable[LegacyParty],
    mapped: Mapping[str, int],
) -> None:
    for row in internal:
        if row.code not in mapped:
            continue
        parent_id = mapped.get(row.parent_code) if row.parent_code else None
        if row.parent_code and parent_id is None:
            continue
        conn.execute(
            text(
                """
                UPDATE internal_entities
                SET parent_party_id=:parent_party_id
                WHERE party_id=:party_id
                """
            ),
            {"parent_party_id": parent_id, "party_id": mapped[row.code]},
        )


def _ensure_tax_profile(
    conn: Connection,
    *,
    party_id: int,
    party_code: str,
    override: TaxProfileOverride | None,
    mapped: Mapping[str, int],
    force_self: bool,
) -> None:
    tax_type = override.tax_type if override else "VAT"
    rows = conn.execute(
        text(
            """
            SELECT id, reporting_party_id, effective_from, effective_to,
                   rule_version, source, reviewed
            FROM party_tax_profiles
            WHERE party_id=:party_id AND tax_type=:tax_type
            ORDER BY effective_from
            """
        ),
        {"party_id": party_id, "tax_type": tax_type},
    ).mappings().all()

    use_override = (
        override is not None
        and not force_self
        and (
            override.reporting_party_code == party_code
            or override.reviewed
        )
        and override.reporting_party_code in mapped
    )
    if use_override:
        assert override is not None
        reporting_id = mapped[override.reporting_party_code]
        desired = {
            "reporting_party_id": reporting_id,
            "effective_from": override.effective_from,
            "effective_to": override.effective_to,
            "rule_version": override.rule_version,
            "source": override.source,
            "reviewed": override.reviewed,
        }
        for row in rows:
            if (
                int(row["reporting_party_id"]) == reporting_id
                and row["effective_from"] == override.effective_from
                and row["effective_to"] == override.effective_to
            ):
                return

        if len(rows) == 1:
            row = rows[0]
            is_default_self = (
                int(row["reporting_party_id"]) == party_id
                and row["effective_from"] == DEFAULT_PROFILE_FROM
                and row["effective_to"] is None
                and _clean(row["source"]) == "V3_TASK06_BACKFILL"
                and not bool(row["reviewed"])
            )
            if is_default_self:
                conn.execute(
                    text("DELETE FROM party_tax_profiles WHERE id=:id"),
                    {"id": int(row["id"])},
                )
                rows = []
        if rows:
            raise ValueError(
                f"{party_code}/{tax_type}: existing tax profile requires manual reconciliation"
            )

        conn.execute(
            text(
                """
                INSERT INTO party_tax_profiles
                    (party_id, tax_type, reporting_party_id, taxpayer_category,
                     tax_registration_id, effective_from, effective_to,
                     rule_version, source, reviewed)
                VALUES
                    (:party_id, :tax_type, :reporting_party_id, NULL, NULL,
                     :effective_from, :effective_to, :rule_version, :source, :reviewed)
                """
            ),
            {
                "party_id": party_id,
                "tax_type": tax_type,
                **desired,
            },
        )
        return

    if rows:
        return
    conn.execute(
        text(
            """
            INSERT INTO party_tax_profiles
                (party_id, tax_type, reporting_party_id, taxpayer_category,
                 tax_registration_id, effective_from, effective_to,
                 rule_version, source, reviewed)
            VALUES
                (:party_id, 'VAT', :party_id, NULL, NULL, :effective_from, NULL,
                 :rule_version, 'V3_TASK06_BACKFILL', false)
            """
        ),
        {
            "party_id": party_id,
            "effective_from": DEFAULT_PROFILE_FROM,
            "rule_version": DEFAULT_RULE_VERSION,
        },
    )


def _audit_summary(conn: Connection, *, actor: str, summary: dict[str, Any]) -> None:
    conn.execute(
        text(
            """
            INSERT INTO audit_logs
                (action, object_type, object_id, message, actor, ip, request_id)
            VALUES
                ('V3_PARTY_BACKFILL', 'PartyMigration', 'TASK06',
                 :message, :actor, '', '')
            """
        ),
        {
            "message": json.dumps(summary, ensure_ascii=False, sort_keys=True),
            "actor": actor[:80],
        },
    )


def apply_backfill(
    engine: Engine,
    *,
    overrides: Mapping[tuple[str, str], TaxProfileOverride],
    confirm_database: str,
    actor: str,
) -> dict[str, Any]:
    with engine.begin() as conn:
        database = str(conn.execute(text("SELECT current_database()")).scalar_one())
        if confirm_database != database:
            raise ValueError(
                f"--confirm-database must exactly match current database {database!r}"
            )

        plan, internal, external = build_plan(conn, overrides)
        _upsert_conflicts(conn, plan["conflicts"])

        blocked_identity = set(plan["blocked_party_codes"])
        profile_blocked = {
            code
            for item in plan["conflicts"]
            if item["blocking"]
            and not item["blocks_party_mapping"]
            and item["conflict_type"].startswith(("TAX_PROFILE_", "REPORTING_PARTY_"))
            for code in (item["subject_code"], item["related_code"])
            if code
        }

        mapped: dict[str, int] = {}
        inserted_or_reused_internal = 0
        inserted_or_reused_external = 0

        for row in internal:
            if row.code in blocked_identity:
                continue
            party_id = _ensure_party(conn, row)
            mapped[row.code] = party_id
            _ensure_internal_entity(conn, row, party_id)
            _ensure_identifier(
                conn,
                party_id=party_id,
                identifier_type="LEGACY_CODE",
                identifier_value=row.code,
                source_system="entities",
            )
            _ensure_identifier(
                conn,
                party_id=party_id,
                identifier_type="TAX_REGISTRATION_ID",
                identifier_value=row.tax_id,
                source_system="entities",
            )
            inserted_or_reused_internal += 1

        for row in external:
            if row.code in blocked_identity:
                continue
            party_id = _ensure_party(conn, row)
            mapped[row.code] = party_id
            _ensure_external_bridge(conn, row, party_id)
            _ensure_identifier(
                conn,
                party_id=party_id,
                identifier_type="LEGACY_CODE",
                identifier_value=row.code,
                source_system="external_parties",
            )
            _ensure_identifier(
                conn,
                party_id=party_id,
                identifier_type="TAX_REGISTRATION_ID",
                identifier_value=row.tax_id,
                source_system="external_parties",
            )
            inserted_or_reused_external += 1

        _set_parent_links(conn, internal, mapped)

        for row in internal:
            party_id = mapped.get(row.code)
            if party_id is None:
                continue
            override = overrides.get((row.code, "VAT"))
            _ensure_tax_profile(
                conn,
                party_id=party_id,
                party_code=row.code,
                override=override,
                mapped=mapped,
                force_self=row.code in profile_blocked,
            )

        summary = {
            "database": database,
            "internal_mapped_or_reused": inserted_or_reused_internal,
            "external_mapped_or_reused": inserted_or_reused_external,
            "blocking_conflict_count": plan["blocking_conflict_count"],
            "review_conflict_count": plan["review_conflict_count"],
        }
        _audit_summary(conn, actor=actor, summary=summary)

        return {
            "status": "PARTIAL" if plan["blocking_conflict_count"] else "APPLIED",
            **summary,
            "plan": plan,
        }


def _render(payload: dict[str, Any], path: str | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="V3 Task 06 Party backfill")
    parser.add_argument("--tax-profile-manifest")
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database", default="")
    parser.add_argument("--actor", default="admin")
    args = parser.parse_args()

    if args.apply and not args.confirm_database:
        parser.error("--apply requires --confirm-database")

    try:
        overrides = load_tax_profile_overrides(args.tax_profile_manifest)
        engine = _engine()
        try:
            if not args.apply:
                with engine.connect() as conn:
                    plan, _, _ = build_plan(conn, overrides)
                _render(plan, args.json_path)
                return 1 if plan["status"] == "FAIL" else 0

            result = apply_backfill(
                engine,
                overrides=overrides,
                confirm_database=args.confirm_database,
                actor=_clean(args.actor) or "admin",
            )
            _render(result, args.json_path)
            return 1 if result["status"] == "PARTIAL" else 0
        finally:
            engine.dispose()
    except (ValueError, json.JSONDecodeError) as exc:
        _render({"status": "FAIL", "error": str(exc)}, args.json_path)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
