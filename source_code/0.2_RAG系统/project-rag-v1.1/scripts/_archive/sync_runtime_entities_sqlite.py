#!/usr/bin/env python3
"""Synchronize canonical entities into the ProjectRAG runtime database.

``data/entities.db`` is a source/cache that contains 26 canonical companies
plus five real external counterparties.  The application runtime is
``data/projectrag.db``.  This command copies only the canonical 26 rows into
the runtime ``entities`` table; external parties remain in the source cache
and are never eligible for canonical entity selectors.

The source and target paths are explicit on purpose.  A SQLite backup is made
with the backup API before a live target transaction starts.  Existing
projects, documents, query logs, and other business tables are not touched.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.import_entities import (  # noqa: E402
    CANONICAL_ENTITY_CODES,
    VIRTUAL_CODES as VIRTUAL_ENTITY_CODES,
    is_canonical_entity_code,
    normalize_entity_code,
)


class RuntimeEntitySyncError(RuntimeError):
    """Raised when source/target identity or schema validation fails."""


TARGET_COLUMNS = (
    "entity_code", "name", "short_name", "entity_type", "industry",
    "business_role", "entity_kind", "legal_entity", "parent_entity_code",
    "status", "legal_representative", "legal_rep_id", "legal_rep_phone",
    "shareholders", "supervisor", "finance_officer", "registered_capital",
    "establishment_date", "acquisition_date", "registration_authority",
    "registration_number", "unified_social_credit_code", "tax_id",
    "business_scope", "contributed_legal", "contributed_shareholder", "note",
    "source", "data_as_of", "created_at", "updated_at",
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _backup_sqlite(database: Path, backup_dir: Path) -> Path | None:
    """Create a consistent backup using SQLite's backup API."""
    if not database.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{database.stem}.pre-runtime-entity-sync-{stamp}.db"
    suffix = 1
    while destination.exists():
        destination = backup_dir / (
            f"{database.stem}.pre-runtime-entity-sync-{stamp}-{suffix}.db"
        )
        suffix += 1
    source = sqlite3.connect(str(database))
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    return destination


def _read_source(source_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not source_path.is_file():
        raise RuntimeEntitySyncError(f"source database not found: {source_path}")
    uri = f"file:{source_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        columns = {row[1] for row in connection.execute("PRAGMA table_info(entities)")}
        if "entities" not in {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }:
            raise RuntimeEntitySyncError(f"source has no entities table: {source_path}")
        if "entity_code" not in columns or "tax_id" not in columns:
            raise RuntimeEntitySyncError("source entities table lacks canonical identity columns")
        rows = [dict(row) for row in connection.execute("SELECT * FROM entities ORDER BY id")]

    canonical: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    for row in rows:
        code = normalize_entity_code(row.get("entity_code"))
        if code in VIRTUAL_ENTITY_CODES:
            raise RuntimeEntitySyncError(f"source contains virtual entity code: {code}")
        if code is None:
            external.append(row)
            continue
        if not is_canonical_entity_code(code):
            raise RuntimeEntitySyncError(f"source contains unknown entity code: {code}")
        row["entity_code"] = code
        canonical.append(row)

    codes = [row["entity_code"] for row in canonical]
    if set(codes) != set(CANONICAL_ENTITY_CODES) or len(codes) != len(CANONICAL_ENTITY_CODES):
        raise RuntimeEntitySyncError(
            f"source canonical roster mismatch: count={len(codes)}, "
            f"missing={sorted(CANONICAL_ENTITY_CODES - set(codes))}, "
            f"extra={sorted(set(codes) - CANONICAL_ENTITY_CODES)}"
        )
    if len(external) != 5:
        raise RuntimeEntitySyncError(
            f"expected 5 external parties in source, found {len(external)}"
        )
    if any(_text(row.get("entity_kind")) != "external_party" for row in external):
        raise RuntimeEntitySyncError("source external rows must be entity_kind=external_party")

    by_tax: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for row in canonical:
        tax_id = _text(row.get("tax_id") or row.get("unified_social_credit_code"))
        if not tax_id:
            raise RuntimeEntitySyncError(f"canonical {row['entity_code']} has no tax_id")
        previous = by_tax.get(tax_id)
        if previous and previous != row["entity_code"]:
            raise RuntimeEntitySyncError(
                f"source duplicate tax_id {tax_id}: {previous}, {row['entity_code']}"
            )
        by_tax[tax_id] = row["entity_code"]
        name = _text(row.get("name"))
        if not name:
            raise RuntimeEntitySyncError(f"canonical {row['entity_code']} has no name")
        previous_name = by_name.get(name)
        if previous_name and previous_name != row["entity_code"]:
            raise RuntimeEntitySyncError(
                f"source duplicate canonical name {name}: {previous_name}, {row['entity_code']}"
            )
        by_name[name] = row["entity_code"]
        role = _text(row.get("business_role"))
        if role and role.upper() != row["entity_code"][0]:
            raise RuntimeEntitySyncError(
                f"canonical {row['entity_code']} has mismatched business_role {role!r}"
            )
        if row["entity_code"] == "A04":
            if bool(row.get("legal_entity")) or normalize_entity_code(row.get("parent_entity_code")) != "A03":
                raise RuntimeEntitySyncError("source A04 must be legal_entity=false with parent A03")
        elif row.get("parent_entity_code"):
            raise RuntimeEntitySyncError(
                f"only A04 may have parent_entity_code, got {row['entity_code']}"
            )
    if sum(1 for row in canonical if bool(row.get("legal_entity"))) != 25:
        raise RuntimeEntitySyncError("source canonical roster must contain 25 legal entities and one branch")
    return canonical, external


def _target_columns(connection: sqlite3.Connection) -> set[str]:
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "entities" not in tables:
        raise RuntimeEntitySyncError("runtime database has no entities table; run Alembic first")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(entities)")}
    missing = {"entity_code", "name", "tax_id"} - columns
    if missing:
        raise RuntimeEntitySyncError(f"runtime entities table lacks columns: {sorted(missing)}")
    return columns


def _desired_row(source: dict[str, Any], columns: set[str], now: str) -> dict[str, Any]:
    code = source["entity_code"]
    tax_id = _text(source.get("tax_id") or source.get("unified_social_credit_code"))
    desired = {column: source.get(column) for column in TARGET_COLUMNS if column in columns}
    desired["entity_code"] = code
    desired["business_role"] = _text(source.get("business_role")) or code[0]
    desired["entity_kind"] = "branch" if code == "A04" else (_text(source.get("entity_kind")) or "company")
    desired["legal_entity"] = 1 if bool(source.get("legal_entity", True)) else 0
    desired["parent_entity_code"] = normalize_entity_code(source.get("parent_entity_code"))
    desired["status"] = _text(source.get("status")) or "active"
    desired["tax_id"] = tax_id
    desired["unified_social_credit_code"] = tax_id
    desired["contributed_legal"] = source.get("contributed_legal") or 0.0
    desired["contributed_shareholder"] = source.get("contributed_shareholder") or 0.0
    desired["source"] = _text(source.get("source")) or "canonical_master"
    desired["created_at"] = now
    desired["updated_at"] = now
    return desired


def _row_diff(current: sqlite3.Row, desired: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for column, value in desired.items():
        if column == "created_at":
            continue
        old = current[column]
        if column == "legal_entity":
            old, value = int(bool(old)), int(bool(value))
        if old != value:
            changed[column] = value
    return changed


def _resolve_unique_runtime_reference(
    connection: sqlite3.Connection,
    value: str,
) -> sqlite3.Row | None:
    """Resolve one canonical runtime row by code, tax id, or exact name.

    This is deliberately strict: a name or tax id that maps to multiple rows
    is an integrity error, never a ``first()`` convenience lookup.  ``None``
    means no runtime row currently matches the reference.
    """
    raw = _text(value)
    if not raw:
        return None
    code = normalize_entity_code(raw)
    if is_canonical_entity_code(code):
        rows = connection.execute(
            "SELECT * FROM entities WHERE entity_code = ?", (code,)
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT * FROM entities
            WHERE tax_id = ? OR name = ? OR short_name = ?
            ORDER BY id
            """,
            (raw, raw, raw),
        ).fetchall()
    if len(rows) > 1:
        raise RuntimeEntitySyncError(
            f"runtime reference is not unique for {raw!r}: {len(rows)} matches"
        )
    return rows[0] if rows else None


def sync_runtime_entities(
    source_path: str | Path,
    runtime_path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Synchronize canonical source rows into the runtime database atomically."""
    source = Path(source_path).expanduser().resolve()
    runtime = Path(runtime_path).expanduser().resolve()
    if source == runtime:
        raise RuntimeEntitySyncError("source and runtime database must be different")
    canonical, external = _read_source(source)
    backup = _backup_sqlite(
        runtime,
        Path(backup_dir).expanduser().resolve() if backup_dir else runtime.parent / "backups",
    )
    report: dict[str, Any] = {
        "source_path": str(source),
        "runtime_path": str(runtime),
        "backup_path": str(backup) if backup else None,
        "dry_run": dry_run,
        "committed": False,
        "created": 0,
        "updated": 0,
        "canonical_rows": len(canonical),
        "external_source_rows_not_imported": len(external),
    }
    if not runtime.is_file():
        raise RuntimeEntitySyncError(f"runtime database not found: {runtime}")

    connection = sqlite3.connect(str(runtime))
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        columns = _target_columns(connection)
        connection.execute("BEGIN IMMEDIATE")
        now = _now()
        for source_row in canonical:
            code = source_row["entity_code"]
            tax_id = _text(source_row.get("tax_id") or source_row.get("unified_social_credit_code"))
            by_code = connection.execute(
                "SELECT * FROM entities WHERE entity_code = ?", (code,)
            ).fetchall()
            by_tax = connection.execute(
                "SELECT * FROM entities WHERE tax_id = ?", (tax_id,)
            ).fetchall()
            if len(by_code) > 1 or len(by_tax) > 1:
                raise RuntimeEntitySyncError(f"runtime identity is not unique for {code}")
            current = by_code[0] if by_code else (by_tax[0] if by_tax else None)
            if current is not None and current["entity_code"] not in (None, code):
                raise RuntimeEntitySyncError(
                    f"runtime identity conflict for {code}: existing code={current['entity_code']}"
                )
            if by_code and _text(current["tax_id"]) not in {"", tax_id}:
                raise RuntimeEntitySyncError(
                    f"runtime code/tax_id conflict for {code}: "
                    f"existing={current['tax_id']}, source={tax_id}"
                )
            if current is None:
                by_name = connection.execute(
                    "SELECT * FROM entities WHERE name = ?", (_text(source_row.get("name")),)
                ).fetchall()
                if len(by_name) > 1:
                    raise RuntimeEntitySyncError(f"runtime duplicate name for {code}")
                current = by_name[0] if by_name else None
                if current is not None and current["entity_code"] not in (None, code):
                    raise RuntimeEntitySyncError(
                        f"runtime name conflict for {code}: existing code={current['entity_code']}"
                    )
                if current is not None and _text(current["tax_id"]) not in {"", tax_id}:
                    raise RuntimeEntitySyncError(
                        f"runtime name/tax_id conflict for {code}: "
                        f"existing={current['tax_id']}, source={tax_id}"
                    )
            desired = _desired_row(source_row, columns, now)
            if current is None:
                insert_columns = [column for column in desired if column in columns]
                placeholders = ", ".join("?" for _ in insert_columns)
                quoted = ", ".join(f'"{column}"' for column in insert_columns)
                connection.execute(
                    f"INSERT INTO entities ({quoted}) VALUES ({placeholders})",
                    [desired[column] for column in insert_columns],
                )
                report["created"] += 1
            else:
                changed = _row_diff(current, desired)
                if changed:
                    assignments = ", ".join(f'"{column}" = ?' for column in changed)
                    connection.execute(
                        f"UPDATE entities SET {assignments} WHERE id = ?",
                        [changed[column] for column in changed] + [current["id"]],
                    )
                    report["updated"] += 1

        codes = {
            _text(row[0]) for row in connection.execute(
                "SELECT entity_code FROM entities WHERE entity_code IS NOT NULL"
            )
        }
        if codes != set(CANONICAL_ENTITY_CODES):
            raise RuntimeEntitySyncError(
                f"runtime canonical roster mismatch after sync: "
                f"missing={sorted(CANONICAL_ENTITY_CODES - codes)}, "
                f"extra={sorted(codes - CANONICAL_ENTITY_CODES)}"
            )
        duplicates = connection.execute(
            """
            SELECT tax_id, COUNT(*) FROM entities
            WHERE tax_id IS NOT NULL AND trim(tax_id) <> ''
            GROUP BY tax_id HAVING COUNT(*) > 1
            """
        ).fetchall()
        if duplicates:
            raise RuntimeEntitySyncError(f"runtime duplicate tax_id values: {duplicates}")
        a04 = connection.execute(
            "SELECT legal_entity, parent_entity_code, entity_kind FROM entities WHERE entity_code='A04'"
        ).fetchone()
        if not a04 or bool(a04[0]) or a04[1] != "A03" or a04[2] != "branch":
            raise RuntimeEntitySyncError("runtime A04 branch semantics are invalid")
        if sum(
            1
            for row in connection.execute(
                "SELECT legal_entity FROM entities WHERE entity_code IS NOT NULL"
            )
            if bool(row[0])
        ) != 25:
            raise RuntimeEntitySyncError(
                "runtime canonical roster must contain 25 legal entities and one branch"
            )
        # Verify both human-readable and machine identity references resolve
        # uniquely to the same canonical code after the write.  This catches
        # stale legacy rows that would otherwise make a UI/name lookup
        # ambiguous while code-based selectors still appear healthy.
        for source_row in canonical:
            expected_code = source_row["entity_code"]
            for reference in (
                _text(source_row.get("name")),
                _text(source_row.get("tax_id") or source_row.get("unified_social_credit_code")),
            ):
                resolved = _resolve_unique_runtime_reference(connection, reference)
                if resolved is None or resolved["entity_code"] != expected_code:
                    raise RuntimeEntitySyncError(
                        f"runtime reference {reference!r} does not resolve uniquely to {expected_code}"
                    )
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
            report["committed"] = True
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize only canonical entities.db rows into projectrag.db"
    )
    parser.add_argument("--source", required=True, help="Source entities.db (read-only)")
    parser.add_argument("--runtime", required=True, help="Runtime projectrag.db")
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = sync_runtime_entities(
            args.source,
            args.runtime,
            backup_dir=args.backup_dir or None,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
