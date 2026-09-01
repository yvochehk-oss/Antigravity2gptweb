#!/usr/bin/env python3
"""Gate S1 verifier for the V3 Party/Taxpayer migration.

Read-only. The verifier checks mapping coverage, bridge integrity, hierarchy
cycles, VAT profile coverage/resolution and current Task 06 conflict state.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.domain.party.resolver import (
    PartyResolutionError,
    TaxProfileWindow,
    assert_acyclic_parent_map,
    resolve_reporting_party,
)

NONBLOCKING_OPEN_TYPES = {"EMPTY_TAX_ID", "INACTIVE_ENTITY", "SHARED_TAX_ID"}


def _database_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        raise SystemExit("DATABASE_URL is required")
    if make_url(raw).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S1 is PostgreSQL-only")
    return raw


def _as_date(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit("--as-of must be YYYY-MM-DD") from exc


def run(as_of: date) -> dict:
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    failures: list[str] = []
    explanations: list[str] = []

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                internal = conn.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) AS source_total,
                            COUNT(*) FILTER (WHERE e.active) AS source_active,
                            COUNT(*) FILTER (WHERE NOT e.active) AS source_inactive,
                            COUNT(*) FILTER (
                                WHERE ie.party_id IS NOT NULL
                                  AND p.code=e.code
                                  AND p.party_type='internal'
                            ) AS mapped_total,
                            COUNT(*) FILTER (
                                WHERE e.active
                                  AND ie.party_id IS NOT NULL
                                  AND p.code=e.code
                                  AND p.party_type='internal'
                            ) AS mapped_active
                        FROM entities e
                        LEFT JOIN internal_entities ie ON ie.canonical_code=e.code
                        LEFT JOIN parties p ON p.id=ie.party_id
                        """
                    )
                ).mappings().one()

                external = conn.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) AS source_total,
                            COUNT(*) FILTER (
                                WHERE ep.party_id IS NOT NULL
                                  AND p.id=ep.party_id
                                  AND p.code=ep.code
                                  AND p.party_type='external'
                            ) AS mapped_total
                        FROM external_parties ep
                        LEFT JOIN parties p ON p.id=ep.party_id
                        """
                    )
                ).mappings().one()

                parent_rows = conn.execute(
                    text("SELECT party_id, parent_party_id FROM internal_entities")
                ).all()
                parent_map = {
                    int(row[0]): int(row[1]) if row[1] is not None else None
                    for row in parent_rows
                }
                try:
                    assert_acyclic_parent_map(parent_map)
                    parent_acyclic = True
                except PartyResolutionError as exc:
                    parent_acyclic = False
                    failures.append(str(exc))

                profile_rows = conn.execute(
                    text(
                        """
                        SELECT party_id, tax_type, reporting_party_id,
                               effective_from, effective_to, reviewed
                        FROM party_tax_profiles
                        WHERE tax_type='VAT'
                        ORDER BY party_id, effective_from
                        """
                    )
                ).mappings().all()
                profiles = tuple(
                    TaxProfileWindow(
                        party_id=int(row["party_id"]),
                        tax_type=str(row["tax_type"]),
                        reporting_party_id=int(row["reporting_party_id"]),
                        effective_from=row["effective_from"],
                        effective_to=row["effective_to"],
                        reviewed=bool(row["reviewed"]),
                    )
                    for row in profile_rows
                )

                internal_party_ids = [
                    int(row[0])
                    for row in conn.execute(
                        text(
                            """
                            SELECT ie.party_id
                            FROM internal_entities ie
                            JOIN parties p ON p.id=ie.party_id
                            WHERE p.party_type='internal'
                            ORDER BY ie.party_id
                            """
                        )
                    ).all()
                ]
                profile_party_ids = {profile.party_id for profile in profiles}
                missing_profiles = sorted(set(internal_party_ids) - profile_party_ids)
                if missing_profiles:
                    failures.append(
                        f"internal parties missing VAT profile: {missing_profiles}"
                    )

                reporting_resolution: dict[str, int] = {}
                reporting_acyclic = True
                for party_id in internal_party_ids:
                    try:
                        reporting_resolution[str(party_id)] = resolve_reporting_party(
                            party_id,
                            as_of,
                            profiles,
                            tax_type="VAT",
                            require_reviewed=False,
                        )
                    except PartyResolutionError as exc:
                        reporting_acyclic = False
                        failures.append(str(exc))

                conflict_rows = conn.execute(
                    text(
                        """
                        SELECT conflict_type,
                               COUNT(*) AS row_count,
                               BOOL_OR(COALESCE((details->>'blocking')::boolean, true))
                                   AS blocking
                        FROM party_migration_conflicts
                        WHERE detector='TASK06_BACKFILL' AND status='OPEN'
                        GROUP BY conflict_type
                        ORDER BY conflict_type
                        """
                    )
                ).mappings().all()

                open_conflicts = [
                    {
                        "conflict_type": str(row["conflict_type"]),
                        "row_count": int(row["row_count"]),
                        "blocking": bool(row["blocking"]),
                    }
                    for row in conflict_rows
                ]
                for item in open_conflicts:
                    if item["blocking"] and item["conflict_type"] not in NONBLOCKING_OPEN_TYPES:
                        failures.append(
                            f"open blocking conflict {item['conflict_type']}={item['row_count']}"
                        )
                    else:
                        explanations.append(
                            f"open review item {item['conflict_type']}={item['row_count']}"
                        )

                if int(internal["mapped_active"] or 0) != int(internal["source_active"] or 0):
                    failures.append(
                        "active internal Party coverage is not 100%: "
                        f"{int(internal['mapped_active'] or 0)}/"
                        f"{int(internal['source_active'] or 0)}"
                    )
                if int(internal["mapped_total"] or 0) != int(internal["source_total"] or 0):
                    explanations.append(
                        "inactive/other internal mapping coverage: "
                        f"{int(internal['mapped_total'] or 0)}/"
                        f"{int(internal['source_total'] or 0)}"
                    )
                if int(external["mapped_total"] or 0) != int(external["source_total"] or 0):
                    failures.append(
                        "ExternalParty bridge coverage is not 100%: "
                        f"{int(external['mapped_total'] or 0)}/"
                        f"{int(external['source_total'] or 0)}"
                    )

                status = "FAIL" if failures else ("EXPLAINED" if explanations else "PASS")
                return {
                    "status": status,
                    "as_of": as_of.isoformat(),
                    "internal": {key: int(value or 0) for key, value in internal.items()},
                    "external": {key: int(value or 0) for key, value in external.items()},
                    "parent_acyclic": parent_acyclic,
                    "reporting_acyclic": reporting_acyclic,
                    "vat_profile_count": len(profiles),
                    "missing_vat_profile_party_ids": missing_profiles,
                    "reporting_resolution": reporting_resolution,
                    "open_conflicts": open_conflicts,
                    "failures": failures,
                    "explanations": explanations,
                }
            finally:
                conn.rollback()
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify V3 Gate S1")
    parser.add_argument("--as-of")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    result = run(_as_date(args.as_of))
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
