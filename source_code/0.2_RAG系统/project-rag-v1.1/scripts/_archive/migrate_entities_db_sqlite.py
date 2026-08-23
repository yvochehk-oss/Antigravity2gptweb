#!/usr/bin/env python3
"""Safely migrate the RAG entity cache to the canonical company master.

The RAG ``entities`` table is a cache, while the tax/facts system owns the
canonical master.  Older SQLite caches can have the legacy schema (including
only a unique display name), missing canonical columns, blank identifiers, or
the retired A/B/C/D placeholder rows.  This migration is deliberately
explicit and transactional:

* the caller supplies both the database and the canonical master file;
* a SQLite backup is created before any write;
* existing rows are matched by canonical code, tax id, or unique exact name;
* display names from an existing cache are preserved and are never replaced
  merely because a legacy virtual code is present;
* unresolved/external rows remain rows without an ``entity_code``;
* canonical code and tax-id uniqueness is checked before commit; and
* ``--dry-run`` executes the complete migration and rolls it back.

This script does not touch the live database unless the caller explicitly
passes its path.  It never calls the API importer, so a failed migration
cannot leave a partially updated service-side cache.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.import_entities import (  # noqa: E402
    BUSINESS_ROLE_CODES,
    CANONICAL_ENTITY_CODES,
    is_canonical_entity_code,
    load_master_rows,
    normalize_entity_code,
    normalize_master_rows,
    validate_master_entities,
)


VIRTUAL_ENTITY_VALUES = frozenset({"A", "B", "C", "D", "甲", "乙", "丙", "丁"})
LEGACY_ROLE_TO_CODE = {"A": "A08", "B": "B01", "C": "C01", "D": "D01"}
PLACEHOLDER_NAME_RE = re.compile(r"^(?:主体|公司|法人)?[ABCD甲乙丙丁](?:方|类|主体)?$", re.IGNORECASE)

# Columns used by both the old and current SQLite cache.  The migration adds
# only missing optional columns and leaves all unrelated data untouched.
OPTIONAL_COLUMNS: dict[str, str] = {
    "entity_code": "TEXT",
    "business_role": "TEXT DEFAULT ''",
    "entity_kind": "TEXT DEFAULT 'company'",
    "legal_entity": "INTEGER DEFAULT 1",
    "parent_entity_code": "TEXT",
    "status": "TEXT DEFAULT 'active'",
    "tax_id": "TEXT",
}

MASTER_COLUMNS = (
    "entity_code", "name", "short_name", "entity_type", "industry", "business_role",
    "entity_kind", "legal_entity", "parent_entity_code", "status", "legal_representative",
    "legal_rep_id", "legal_rep_phone", "shareholders", "supervisor", "finance_officer",
    "registered_capital", "establishment_date", "acquisition_date", "registration_authority",
    "registration_number", "unified_social_credit_code", "tax_id", "business_scope",
    "contributed_legal", "contributed_shareholder", "note", "source", "data_as_of",
)


class EntityMigrationError(RuntimeError):
    """Raised when migration preflight or postflight validation fails."""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sqlite_backup(db_path: Path, backup_dir: Path) -> Path | None:
    """Create a consistent SQLite backup using the SQLite backup API."""
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{db_path.stem}.pre-canonical-{stamp}.db"
    # Avoid a same-second collision while keeping the path readable.
    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{db_path.stem}.pre-canonical-{stamp}-{suffix}.db"
        suffix += 1
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    return backup_path


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(entities)").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_schema(conn: sqlite3.Connection) -> set[str]:
    """Create the cache table if absent and add canonical columns safely."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            short_name TEXT DEFAULT '',
            entity_type TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            legal_representative TEXT DEFAULT '',
            legal_rep_id TEXT DEFAULT '',
            legal_rep_phone TEXT DEFAULT '',
            shareholders TEXT DEFAULT '',
            supervisor TEXT DEFAULT '',
            finance_officer TEXT DEFAULT '',
            registered_capital TEXT DEFAULT '',
            establishment_date TEXT DEFAULT '',
            acquisition_date TEXT DEFAULT '',
            registration_authority TEXT DEFAULT '',
            registration_number TEXT DEFAULT '',
            unified_social_credit_code TEXT DEFAULT '',
            business_scope TEXT DEFAULT '',
            contributed_legal REAL DEFAULT 0.0,
            contributed_shareholder REAL DEFAULT 0.0,
            note TEXT DEFAULT '',
            source TEXT DEFAULT 'excel',
            data_as_of TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            entity_code TEXT,
            business_role TEXT DEFAULT '',
            entity_kind TEXT DEFAULT 'company',
            legal_entity INTEGER DEFAULT 1,
            parent_entity_code TEXT,
            status TEXT DEFAULT 'active',
            tax_id TEXT
        )
        """
    )
    columns = _table_columns(conn)
    if "name" not in columns:
        raise EntityMigrationError("entities table has no required name column")
    for column, declaration in OPTIONAL_COLUMNS.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE entities ADD COLUMN {column} {declaration}")
    columns = _table_columns(conn)

    return columns


def _drop_identifier_indexes(conn: sqlite3.Connection) -> None:
    """Drop known/current identifier indexes before canonical reassignment."""
    index_rows = conn.execute("PRAGMA index_list(entities)").fetchall()
    for row in index_rows:
        index_name = str(row[1])
        unique = bool(row[2])
        # SQLite implements UNIQUE constraints with an auto-index.  It is
        # owned by the table constraint and cannot be dropped independently.
        # Only remove explicitly-created unique indexes; preserving the
        # constraint is important for an existing runtime database.
        origin = str(row[3]) if len(row) > 3 else ""
        if origin in {"pk", "u"} or index_name.startswith("sqlite_autoindex_"):
            continue
        if not unique:
            continue
        info = conn.execute(f"PRAGMA index_info({index_name!r})").fetchall()
        indexed_columns = {str(item[2]) for item in info}
        if indexed_columns in ({"entity_code"}, {"tax_id"}) and len(info) == 1:
            conn.execute(f'DROP INDEX "{index_name.replace(chr(34), chr(34) * 2)}"')


def _normalize_identifier_values(conn: sqlite3.Connection) -> None:
    """Normalize blank identifiers after old unique indexes are removed."""
    conn.execute("UPDATE entities SET entity_code = NULL WHERE trim(COALESCE(entity_code, '')) = ''")
    conn.execute("UPDATE entities SET tax_id = NULL WHERE trim(COALESCE(tax_id, '')) = ''")
    conn.execute(
        """
        UPDATE entities
        SET tax_id = trim(unified_social_credit_code)
        WHERE tax_id IS NULL
          AND trim(COALESCE(unified_social_credit_code, '')) <> ''
        """
    )


def _duplicate_values(conn: sqlite3.Connection, column: str) -> list[tuple[str, int]]:
    return [
        (str(value), int(count))
        for value, count in conn.execute(
            f"""
            SELECT {column}, COUNT(*)
            FROM entities
            WHERE {column} IS NOT NULL AND trim({column}) <> ''
            GROUP BY {column}
            HAVING COUNT(*) > 1
            """
        ).fetchall()
    ]


def _canonicalize_master_headers(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse decorated XLSX headers to the importer aliases.

    The delivered workbook intentionally uses human-readable headers such as
    ``entity_code\\n规范单位编码``.  The shared importer also supports plain
    aliases, so this adapter keeps the migration compatible with both
    workbook variants without changing that importer.
    """
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("entity_code", ("entity_code", "规范单位编码", "实体代码", "主体代码")),
        ("name", ("公司全称", "公司名称", "entity_name", "company_name")),
        ("business_role", ("business_role", "业务角色", "ABCD主体分类", "主体类别代码", "主体分类")),
        ("tax_id", ("统一社会信用代码", "纳税人识别号", "tax_id", "unified_social_credit_code")),
        ("entity_type", ("企业单位性质", "企业类型", "entity_type")),
        ("industry", ("行业细分领域", "industry")),
        ("legal_representative", ("法定代表人/负责人", "法定代表人", "负责人", "legal_representative")),
        ("shareholders", ("主要股东及持股比例", "股东", "shareholders")),
        ("supervisor", ("监事", "supervisor")),
        ("registered_capital", ("注册资本", "registered_capital")),
        ("establishment_date", ("成立/获取时间", "成立日期", "establishment_date")),
        ("registration_authority", ("登记主管机关", "registration_authority")),
        ("business_scope", ("核准法定经营范围", "经营范围", "business_scope")),
        ("entity_kind", ("entity_kind", "单位类型")),
        ("legal_entity", ("legal_entity", "独立法人", "是否独立法人")),
        ("parent_entity_code", ("parent_entity_code", "上级单位编码", "总公司代码")),
        ("status", ("status", "状态")),
    )
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        normalized_keys = {str(key).replace("\\n", "").replace(" ", ""): key for key in row}
        for target, candidates in aliases:
            if row.get(target) not in (None, ""):
                continue
            value = None
            for candidate in candidates:
                candidate_key = candidate.replace("\\n", "").replace(" ", "")
                source_key = normalized_keys.get(candidate_key)
                if source_key is None:
                    source_key = next(
                        (
                            key for key in row
                            if candidate_key and candidate_key in str(key).replace("\\n", "").replace(" ", "")
                        ),
                        None,
                    )
                if source_key is not None and row.get(source_key) not in (None, ""):
                    value = row[source_key]
                    break
            if value not in (None, ""):
                row[target] = value
        output.append(row)
    return output


def _load_master(master_file: str | Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    try:
        raw_rows = _canonicalize_master_headers(load_master_rows(master_file))
        # The delivered workbook contains a title/summary row in the same
        # sheet as the 26 data rows.  Keep rows with a sequence number or an
        # actual identity field; the generic importer cannot infer a role
        # from that summary text and must not be asked to do so.
        filtered_rows = [
            row for row in raw_rows
            if not any(marker in _text(row.get("name")) for marker in ("合计", "汇总"))
            and not _text(row.get("name")).startswith("26家")
            and (isinstance(row.get("序号"), (int, float))
            or _text(row.get("entity_code"))
            or _text(row.get("tax_id"))
            or _text(row.get("business_role")))
        ]
        rows = normalize_master_rows(filtered_rows)
        # JSON/CSV masters may use descriptive values such as
        # ``CONSTRUCTION`` instead of the role code.  The canonical code is
        # authoritative for this aggregation field when the importer cannot
        # extract a literal A/B/C/D marker.
        for row in rows:
            if not _text(row.get("business_role")) and _text(row.get("entity_code")):
                row["business_role"] = _text(row["entity_code"])[0]
        validate_master_entities(rows, require_all_codes=True)
    except Exception as exc:  # turn importer details into one migration error type
        raise EntityMigrationError(f"invalid canonical master: {exc}") from exc

    by_code: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    by_tax_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = normalize_entity_code(row.get("entity_code"))
        name = _text(row.get("name"))
        tax_id = _text(row.get("tax_id") or row.get("unified_social_credit_code"))
        if not code or not name or not tax_id:
            raise EntityMigrationError("canonical master contains an incomplete identity row")
        if code in by_code or name in by_name or tax_id in by_tax_id:
            raise EntityMigrationError(f"canonical master has duplicate code/name/tax_id near {code}")
        by_code[code] = dict(row)
        by_name[name] = dict(row)
        by_tax_id[tax_id] = dict(row)
    return by_code, by_name, by_tax_id


def _master_values(row: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    values = {column: row.get(column) for column in MASTER_COLUMNS}
    # ``Base.metadata.create_all`` defines these as ORM-side defaults, while
    # the Alembic baseline intentionally keeps the columns NOT NULL without a
    # server default.  Supply the zero values explicitly so importing into an
    # existing runtime schema cannot fail on a missing optional amount.
    values["contributed_legal"] = row.get("contributed_legal", 0.0) or 0.0
    values["contributed_shareholder"] = row.get("contributed_shareholder", 0.0) or 0.0
    values["legal_entity"] = 1 if bool(row.get("legal_entity", True)) else 0
    if values.get("entity_code") == "A04":
        values["entity_kind"] = "branch"
        values["legal_entity"] = 0
        values["parent_entity_code"] = "A03"
    values["tax_id"] = _text(row.get("tax_id") or row.get("unified_social_credit_code")) or None
    values["unified_social_credit_code"] = values["tax_id"] or ""
    values["source"] = _text(row.get("source")) or "canonical_master"
    values["status"] = _text(row.get("status")) or "active"
    values["parent_entity_code"] = normalize_entity_code(row.get("parent_entity_code"))
    values["created_at"] = now
    values["updated_at"] = now
    return values


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _placeholder_name(name: str) -> bool:
    return bool(PLACEHOLDER_NAME_RE.fullmatch(_text(name)))


def _resolve_target(
    row: Mapping[str, Any],
    *,
    by_code: Mapping[str, Mapping[str, Any]],
    by_name: Mapping[str, Mapping[str, Any]],
    by_tax_id: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, str]:
    """Resolve an existing row without selecting an arbitrary match."""
    raw_code = normalize_entity_code(row.get("entity_code"))
    name = _text(row.get("name"))
    tax_id = _text(row.get("tax_id") or row.get("unified_social_credit_code"))

    if raw_code in VIRTUAL_ENTITY_VALUES:
        # A known name/tax id is stronger evidence than a retired role code.
        target = by_name.get(name) or by_tax_id.get(tax_id)
        if target is not None:
            return target, "legacy_virtual_resolved_by_name_or_tax_id"
        if raw_code in BUSINESS_ROLE_CODES:
            return by_code[LEGACY_ROLE_TO_CODE[raw_code]], "legacy_role_mapping"
        return None, "placeholder_external"

    if raw_code:
        if not is_canonical_entity_code(raw_code):
            raise EntityMigrationError(f"entity id {row.get('id')} has non-canonical code {raw_code!r}")
        target = by_code.get(raw_code)
        if target is None:
            raise EntityMigrationError(f"entity id {row.get('id')} references unknown canonical code {raw_code}")
        if name and name in by_name and by_name[name].get("entity_code") != raw_code:
            raise EntityMigrationError(f"entity id {row.get('id')} code/name conflict: {raw_code}/{name}")
        if tax_id and tax_id != _text(target.get("tax_id")):
            raise EntityMigrationError(f"entity id {row.get('id')} code/tax id conflict: {raw_code}/{tax_id}")
        return target, "canonical_code"

    # Blank code: exact tax id wins, then exact display name.  Both indexes
    # are unique by construction; no first-row fallback is allowed.
    target_by_tax = by_tax_id.get(tax_id) if tax_id else None
    target_by_name = by_name.get(name) if name else None
    if target_by_tax and target_by_name and target_by_tax.get("entity_code") != target_by_name.get("entity_code"):
        raise EntityMigrationError(f"entity id {row.get('id')} name/tax_id resolve to different companies")
    return target_by_tax or target_by_name, "tax_id_or_name" if (target_by_tax or target_by_name) else "external_unresolved"


def _external_values(row: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    values = dict(row)
    values["entity_code"] = None
    values["business_role"] = ""
    values["entity_kind"] = "external_party"
    values["legal_entity"] = 0
    values["parent_entity_code"] = None
    values["status"] = _text(row.get("status")) or "active"
    values["source"] = _text(row.get("source")) or "external_party_migration"
    values["updated_at"] = now
    # Never persist a role placeholder as a party name.  Preserve the source
    # token in note for audit while giving the cache a non-virtual display name.
    original_name = _text(row.get("name"))
    if _placeholder_name(original_name):
        values["name"] = f"Unresolved external party {row.get('id')}"
        previous_note = _text(row.get("note"))
        values["note"] = f"legacy placeholder name={original_name}" + (f"; {previous_note}" if previous_note else "")
    return values


def _merge_internal_values(
    row: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    """Fill canonical fields while preserving an existing display name."""
    values = dict(row)
    canonical = _master_values(target, now=now)
    for column, value in canonical.items():
        if column in {"created_at", "updated_at"}:
            continue
        # Name is evidence/display data from the existing cache.  Do not
        # replace it because a legacy role code maps to a canonical code.
        if column == "name" and _text(row.get("name")):
            if not _placeholder_name(_text(row.get("name"))):
                continue
        if column in {"short_name", "legal_representative", "legal_rep_id", "legal_rep_phone",
                      "shareholders", "supervisor", "finance_officer", "registered_capital",
                      "establishment_date", "acquisition_date", "registration_authority",
                      "registration_number", "business_scope", "note", "data_as_of"}:
            if _text(row.get(column)):
                continue
        values[column] = value
    values["entity_code"] = target["entity_code"]
    values["business_role"] = target.get("business_role") or target["entity_code"][0]
    values["entity_kind"] = "branch" if target["entity_code"] == "A04" else (target.get("entity_kind") or "company")
    values["legal_entity"] = 1 if bool(target.get("legal_entity", True)) else 0
    values["parent_entity_code"] = normalize_entity_code(target.get("parent_entity_code"))
    values["status"] = _text(target.get("status")) or "active"
    values["tax_id"] = _text(target.get("tax_id") or target.get("unified_social_credit_code")) or None
    values["unified_social_credit_code"] = values["tax_id"] or ""
    values["updated_at"] = now
    if not _text(row.get("created_at")):
        values["created_at"] = now
    return values


def _changed_values(current: Mapping[str, Any], desired: Mapping[str, Any], columns: Iterable[str]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for column in columns:
        if column not in desired:
            continue
        old = current.get(column)
        new = desired.get(column)
        # SQLite returns 0/1 while the master may provide bools.
        if column == "legal_entity":
            old = 1 if bool(old) else 0
            new = 1 if bool(new) else 0
        if old != new:
            changed[column] = new
    return changed


def _apply_row_update(
    conn: sqlite3.Connection,
    row: Mapping[str, Any],
    desired: Mapping[str, Any],
    columns: set[str],
) -> bool:
    update_columns = [column for column in desired if column in columns and column != "id"]
    changed = _changed_values(row, desired, update_columns)
    changed_without_timestamp = {key: value for key, value in changed.items() if key != "updated_at"}
    if not changed_without_timestamp:
        return False
    if "updated_at" in columns:
        changed["updated_at"] = desired.get("updated_at")
    assignments = ", ".join(f'"{column}" = ?' for column in changed)
    conn.execute(
        f'UPDATE entities SET {assignments} WHERE id = ?',
        [changed[column] for column in changed] + [row["id"]],
    )
    return True


def _insert_row(conn: sqlite3.Connection, desired: Mapping[str, Any], columns: set[str]) -> None:
    insert_columns = [column for column in desired if column in columns and column != "id"]
    placeholders = ", ".join("?" for _ in insert_columns)
    quoted = ", ".join(f'"{column}"' for column in insert_columns)
    conn.execute(
        f"INSERT INTO entities ({quoted}) VALUES ({placeholders})",
        [desired[column] for column in insert_columns],
    )


def _create_identifier_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX uq_entities_entity_code ON entities(entity_code) WHERE entity_code IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX uq_entities_tax_id ON entities(tax_id) WHERE tax_id IS NOT NULL"
    )


def _postflight(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT entity_code, business_role, legal_entity, parent_entity_code, tax_id, name FROM entities").fetchall()
    codes = {str(row[0]) for row in rows if row[0] is not None and str(row[0]).strip()}
    if codes != set(CANONICAL_ENTITY_CODES):
        raise EntityMigrationError(
            f"postflight canonical code roster mismatch: missing={sorted(set(CANONICAL_ENTITY_CODES) - codes)}, "
            f"extra={sorted(codes - set(CANONICAL_ENTITY_CODES))}"
        )
    duplicates = _duplicate_values(conn, "tax_id")
    if duplicates:
        raise EntityMigrationError(f"postflight duplicate tax_id values: {duplicates}")
    a04 = conn.execute(
        "SELECT legal_entity, parent_entity_code, entity_kind FROM entities WHERE entity_code = 'A04'"
    ).fetchone()
    if not a04 or bool(a04[0]) or a04[1] != "A03" or a04[2] != "branch":
        raise EntityMigrationError("postflight A04 branch semantics are invalid")
    if any(str(row[0]).strip() in VIRTUAL_ENTITY_VALUES for row in rows if row[0] is not None):
        raise EntityMigrationError("postflight still contains a virtual entity code")
    role_counts = Counter(str(row[1] or "") for row in rows if row[0] is not None)
    return {
        "entity_rows": len(rows),
        "canonical_rows": len(codes),
        "external_rows": sum(1 for row in rows if row[0] is None),
        "role_counts": dict(sorted(role_counts.items())),
        "duplicate_tax_ids": 0,
    }


def migrate_entities_db(
    db_path: str | Path,
    master_file: str | Path,
    *,
    backup_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate one SQLite cache atomically and return an auditable report."""
    path = Path(db_path).expanduser().resolve()
    master_path = Path(master_file).expanduser().resolve()
    if not master_path.is_file():
        raise EntityMigrationError(f"canonical master file not found: {master_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = _sqlite_backup(path, Path(backup_dir).expanduser().resolve() if backup_dir else path.parent / "backups")
    by_code, by_name, by_tax_id = _load_master(master_path)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    report: dict[str, Any] = {
        "db_path": str(path),
        "master_file": str(master_path),
        "backup_path": str(backup) if backup else None,
        "dry_run": dry_run,
        "committed": False,
        "created": 0,
        "updated": 0,
        "external_unresolved": 0,
        "warnings": [],
    }
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        columns = _ensure_schema(conn)
        _drop_identifier_indexes(conn)
        _normalize_identifier_values(conn)

        existing_duplicates = _duplicate_values(conn, "tax_id")
        if existing_duplicates:
            raise EntityMigrationError(f"existing duplicate tax_id values require manual review: {existing_duplicates}")
        existing_rows = [_row_dict(row) for row in conn.execute("SELECT * FROM entities ORDER BY id").fetchall()]
        assigned_codes: dict[str, int] = {}
        now = _now()

        for row in existing_rows:
            target, reason = _resolve_target(row, by_code=by_code, by_name=by_name, by_tax_id=by_tax_id)
            if target is None:
                desired = _external_values(row, now=now)
                report["external_unresolved"] += 1
                if reason == "placeholder_external":
                    report["warnings"].append(f"entity id {row['id']} retained as unresolved external party")
            else:
                code = str(target["entity_code"])
                previous = assigned_codes.get(code)
                if previous is not None and previous != row["id"]:
                    raise EntityMigrationError(f"multiple existing rows resolve to canonical code {code}: {previous}, {row['id']}")
                assigned_codes[code] = int(row["id"])
                existing_tax = _text(row.get("tax_id") or row.get("unified_social_credit_code"))
                target_tax = _text(target.get("tax_id") or target.get("unified_social_credit_code"))
                if existing_tax and existing_tax != target_tax:
                    raise EntityMigrationError(f"entity id {row['id']} tax id conflicts with canonical {code}")
                desired = _merge_internal_values(row, target, now=now)
                if reason == "legacy_role_mapping":
                    report["warnings"].append(
                        f"entity id {row['id']} legacy role {row.get('entity_code')} mapped to {code}; name preserved"
                    )
            if _apply_row_update(conn, row, desired, columns):
                report["updated"] += 1

        # Add canonical companies that were not represented by the old cache.
        for code in sorted(CANONICAL_ENTITY_CODES):
            if code in assigned_codes:
                continue
            desired = _master_values(by_code[code], now=now)
            _insert_row(conn, desired, columns)
            report["created"] += 1

        # Build unique indexes only after every row has been assigned/cleaned.
        _create_identifier_indexes(conn)
        report.update(_postflight(conn))
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
            report["committed"] = True
        return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate a RAG SQLite entity cache to the canonical real-company master")
    parser.add_argument("--db", required=True, help="SQLite entities.db path (explicit; no live default)")
    parser.add_argument("--master-file", required=True, help="Canonical JSON/CSV/XLSX master")
    parser.add_argument("--backup-dir", default="", help="Directory for pre-migration SQLite backup")
    parser.add_argument("--dry-run", action="store_true", help="Run and validate migration, then roll back")
    args = parser.parse_args(argv)
    try:
        report = migrate_entities_db(
            args.db,
            args.master_file,
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
