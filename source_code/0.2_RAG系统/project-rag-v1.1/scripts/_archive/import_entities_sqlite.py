#!/usr/bin/env python3
"""Import the 26-company canonical entity master into the RAG cache.

The source is deliberately explicit: callers must provide ``--master-file``.
There is no personal-machine default path and no name-based identity/upsert.
JSON and CSV are dependency-free; XLSX is supported when ``openpyxl`` is
installed (it is an optional import-time dependency for this script only).

Examples::

    python scripts/import_entities.py --master-file master.json --dry-run
    python scripts/import_entities.py --master-file master.xlsx --db-url sqlite:///data/entities.db
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

# Keep the importer runnable as a standalone JSON/CSV utility.  Importing the
# ORM model here would force a PostgreSQL driver even when the caller only
# wants to validate a local master file.
CANONICAL_ENTITY_CODES = frozenset(
    {f"A{i:02d}" for i in range(1, 12)}
    | {f"B{i:02d}" for i in range(1, 11)}
    | {f"C{i:02d}" for i in range(1, 3)}
    | {f"D{i:02d}" for i in range(1, 4)}
)
BUSINESS_ROLE_CODES = frozenset({"A", "B", "C", "D"})
CANONICAL_ENTITY_CODE_RE = re.compile(r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3]))$")


def normalize_entity_code(value: Any) -> str | None:
    value = _text(value).upper()
    return value or None


def is_canonical_entity_code(value: Any) -> bool:
    code = normalize_entity_code(value)
    return bool(code and code in CANONICAL_ENTITY_CODES and CANONICAL_ENTITY_CODE_RE.fullmatch(code))


ROLE_CODE_RANGES = {
    "A": tuple(f"A{i:02d}" for i in range(1, 12)),
    "B": tuple(f"B{i:02d}" for i in range(1, 11)),
    "C": tuple(f"C{i:02d}" for i in range(1, 3)),
    "D": tuple(f"D{i:02d}" for i in range(1, 4)),
}
VIRTUAL_CODES = frozenset({"A", "B", "C", "D", "甲", "乙", "丙", "丁"})
ROLE_RE = re.compile(r"(?:^|[^A-Za-z])([ABCD])(?:类|类主体|类业务)?(?:$|[^A-Za-z])", re.IGNORECASE)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _pick(row: Mapping[str, Any], *keys: str) -> Any:
    """Pick a value using exact then whitespace-insensitive header aliases."""
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    normalized = {_text(k).replace(" ", ""): v for k, v in row.items()}
    for key in keys:
        value = normalized.get(_text(key).replace(" ", ""))
        if value not in (None, ""):
            return value
    return ""


def parse_legal_rep(value: Any) -> tuple[str, str, str]:
    """Split a representative cell into name, ID and phone."""
    text = _text(value).replace("\n", " ")
    id_match = re.search(r"\b(\d{17}[\dXx])\b", text)
    id_num = id_match.group(1) if id_match else ""
    phone_match = re.search(r"\b(1[3-9]\d{9})\b", text)
    phone = phone_match.group(1) if phone_match else ""
    name = re.sub(r"[\(（](?:A证|过渡)[）\)]", "", text)
    name = re.sub(r"\d{17}[\dXx]|1[3-9]\d{9}", "", name)
    name = re.sub(r"\s+", " ", name).strip().strip("/").strip()
    return name.split("/")[0].strip(), id_num, phone


def parse_shareholder(value: Any) -> str:
    return _text(value).replace("\n", " ")


def _read_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        payload = payload.get("entities", payload.get("items", payload))
    if isinstance(payload, Mapping):
        payload = [
            dict(value, entity_code=key) if isinstance(value, Mapping)
            else {"entity_code": key, "name": value}
            for key, value in payload.items()
        ]
    if not isinstance(payload, list):
        raise ValueError("JSON master must be a list or an entities/items object")
    return [dict(row) for row in payload if isinstance(row, Mapping)]


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _read_xlsx(path: Path) -> list[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on optional env
        raise RuntimeError("XLSX import requires optional dependency openpyxl") from exc

    workbook = load_workbook(path, read_only=True, data_only=True)
    if not workbook.worksheets:
        raise ValueError("XLSX master has no worksheets")
    # Locate a row containing the real company-name and tax-id columns.  The
    # supplied workbook has title rows before row 3, so assuming row 1 would
    # silently import nonsense.
    for sheet in workbook.worksheets:
        values = list(sheet.iter_rows(values_only=True))
        header_index = None
        for index, values_row in enumerate(values[:20]):
            headers = {_text(value) for value in values_row if value is not None}
            if ("公司全称" in headers or "公司名称" in headers or "name" in headers) and (
                "统一社会信用代码" in headers or "tax_id" in headers or "纳税人识别号" in headers
            ):
                header_index = index
                break
        if header_index is None:
            continue
        headers = [_text(value) for value in values[header_index]]
        rows = []
        for values_row in values[header_index + 1 :]:
            if not any(value not in (None, "") for value in values_row):
                continue
            rows.append({headers[i]: values_row[i] if i < len(values_row) else "" for i in range(len(headers)) if headers[i]})
        if rows:
            return rows
    raise ValueError("could not locate a company master header row in XLSX")


def load_master_rows(master_file: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Load source rows from JSON, CSV or optional XLSX."""
    path = Path(master_file).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"master file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _read_json(path)
    if suffix == ".csv":
        return _read_csv(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(path)
    raise ValueError("master file must be JSON, CSV, XLSX or XLSM")


def _role(value: Any) -> str:
    text = _text(value)
    match = re.search(r"\b([ABCD])(?:类|类主体|类业务)?\b", text, re.IGNORECASE)
    if not match:
        match = re.search(r"([ABCD])类", text, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _code_for_row(raw_code: Any, role: str, role_counts: Counter[str]) -> str:
    supplied = normalize_entity_code(_text(raw_code))
    if supplied:
        if supplied in VIRTUAL_CODES or supplied in BUSINESS_ROLE_CODES or not is_canonical_entity_code(supplied):
            raise ValueError(f"virtual or non-canonical entity code is not allowed: {raw_code!r}")
        return supplied
    if role not in ROLE_CODE_RANGES:
        raise ValueError("each master row needs a canonical entity_code or an A/B/C/D business_role")
    role_counts[role] += 1
    values = ROLE_CODE_RANGES[role]
    if role_counts[role] > len(values):
        raise ValueError(f"too many {role} business-role rows for the canonical 26-company master")
    return values[role_counts[role] - 1]


def normalize_master_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Map source headers to canonical cache fields and assign missing codes."""
    role_counts: Counter[str] = Counter()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(rows, 1):
        name = _text(_pick(raw, "entity_name", "name", "company_name", "公司全称", "公司名称"))
        if not name or name in {"合计", "汇总", "/"} or "合计" in name or "汇总" in name or name.startswith("26 家"):
            continue
        role = _role(_pick(raw, "business_role", "role", "ABCD主体分类", "主体类别代码", "主体分类"))
        code = _code_for_row(_pick(raw, "entity_code", "code", "canonical_code", "实体代码", "主体代码"), role, role_counts)
        tax_id = _text(_pick(raw, "tax_id", "unified_social_credit_code", "统一社会信用代码", "纳税人识别号"))
        legal_rep, legal_rep_id, legal_rep_phone = parse_legal_rep(
            _pick(raw, "legal_representative", "legal_rep", "法定代表人/负责人", "法定代表人", "负责人")
        )
        entity_type = _text(_pick(raw, "entity_type", "企业类型", "企业归属属性"))
        is_branch = "分公司" in name or "非独立法人" in entity_type
        raw_legal_entity = _pick(raw, "legal_entity", "is_legal_entity", "独立法人", "是否独立法人")
        if _text(raw_legal_entity):
            legal_entity = _text(raw_legal_entity).lower() not in {"0", "false", "no", "否", "非独立法人"}
        else:
            legal_entity = not is_branch
        parent_code = normalize_entity_code(_pick(raw, "parent_entity_code", "parent_code", "上级法人代码", "总公司代码"))
        if code == "A04" or "重庆分公司" in name:
            parent_code = parent_code or "A03"
            legal_entity = False
        normalized.append({
            "entity_code": code,
            "name": name,
            "short_name": _text(_pick(raw, "short_name", "简称")),
            "entity_type": "分公司" if is_branch else entity_type,
            "industry": _text(_pick(raw, "industry", "行业", "行业细分领域")),
            "business_role": role,
            "entity_kind": "branch" if is_branch else "company",
            "legal_entity": legal_entity,
            "parent_entity_code": parent_code,
            "status": _text(_pick(raw, "status", "状态")) or "active",
            "legal_representative": legal_rep,
            "legal_rep_id": legal_rep_id,
            "legal_rep_phone": legal_rep_phone,
            "shareholders": parse_shareholder(_pick(raw, "shareholders", "主要股东及持股比例", "股东")),
            "supervisor": _text(_pick(raw, "supervisor", "监事")),
            "finance_officer": _text(_pick(raw, "finance_officer", "财务负责人")),
            "registered_capital": _text(_pick(raw, "registered_capital", "注册资本")),
            "establishment_date": _text(_pick(raw, "establishment_date", "成立/获取时间", "成立日期")),
            "acquisition_date": _text(_pick(raw, "acquisition_date", "获取时间")),
            "registration_authority": _text(_pick(raw, "registration_authority", "登记主管机关")),
            "registration_number": _text(_pick(raw, "registration_number", "登记注册号")),
            "unified_social_credit_code": tax_id,
            "tax_id": tax_id or None,
            "business_scope": _text(_pick(raw, "business_scope", "核准法定经营范围", "经营范围")),
            "note": _text(_pick(raw, "note", "备注")),
            "source": _text(_pick(raw, "source", "来源")) or "canonical_master",
            "data_as_of": _text(_pick(raw, "data_as_of", "数据截止日")),
        })
    return normalized


def validate_master_entities(
    entities: Iterable[Mapping[str, Any]],
    *,
    require_all_codes: bool = True,
) -> list[dict[str, Any]]:
    """Validate count, canonical codes, branch semantics and key uniqueness."""
    rows = [dict(row) for row in entities]

    by_code: dict[str, dict[str, Any]] = {}
    by_tax_id: dict[str, str] = {}
    for row in rows:
        code = normalize_entity_code(row.get("entity_code"))
        if not code or not is_canonical_entity_code(code):
            raise ValueError(f"row {row.get('name')!r} has invalid/virtual entity_code {row.get('entity_code')!r}")
        if code in by_code:
            raise ValueError(f"duplicate entity_code {code}")
        name = _text(row.get("name"))
        if not name:
            raise ValueError(f"entity {code} has empty name")
        tax_id = _text(row.get("tax_id") or row.get("unified_social_credit_code"))
        if not tax_id:
            raise ValueError(f"entity {code} has empty tax_id")
        if tax_id in by_tax_id and by_tax_id[tax_id] != code:
            raise ValueError(f"duplicate tax_id {tax_id}: {by_tax_id[tax_id]} and {code}")
        by_tax_id[tax_id] = code
        role = _role(row.get("business_role")) or code[0]
        if role != code[0]:
            raise ValueError(f"entity {code} business_role {role!r} does not match canonical role")
        if code == "A04":
            if bool(row.get("legal_entity", True)) or normalize_entity_code(row.get("parent_entity_code")) != "A03":
                raise ValueError("A04 must be legal_entity=false with parent_entity_code=A03")
        elif row.get("parent_entity_code"):
            raise ValueError(f"only A04 may have parent_entity_code, got {code}")
        by_code[code] = row

    if require_all_codes:
        if len(rows) != 26:
            raise ValueError(f"canonical master must contain exactly 26 units, got {len(rows)}")
        missing = sorted(CANONICAL_ENTITY_CODES - set(by_code))
        extra = sorted(set(by_code) - CANONICAL_ENTITY_CODES)
        if missing or extra:
            raise ValueError(f"canonical code roster mismatch; missing={missing}, extra={extra}")
    return rows


def extract_entities(master_file: str | os.PathLike[str]) -> dict[str, dict[str, Any]]:
    """Compatibility wrapper returning rows keyed by canonical entity code."""
    rows = normalize_master_rows(load_master_rows(master_file))
    validate_master_entities(rows)
    return {row["entity_code"]: row for row in rows}


def _sqlite_path(db_url: str) -> str:
    if db_url.startswith("sqlite:////"):
        return db_url[len("sqlite://") :]
    if db_url.startswith("sqlite:///"):
        return db_url[len("sqlite:///") :]
    if db_url.startswith("sqlite://"):
        return db_url[len("sqlite://") :]
    return db_url


def _ensure_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_code TEXT UNIQUE,
            name TEXT NOT NULL,
            short_name TEXT DEFAULT '', entity_type TEXT DEFAULT '', industry TEXT DEFAULT '',
            business_role TEXT DEFAULT '', entity_kind TEXT DEFAULT 'company', legal_entity INTEGER DEFAULT 1,
            parent_entity_code TEXT, status TEXT DEFAULT 'active',
            legal_representative TEXT DEFAULT '', legal_rep_id TEXT DEFAULT '', legal_rep_phone TEXT DEFAULT '',
            shareholders TEXT DEFAULT '', supervisor TEXT DEFAULT '', finance_officer TEXT DEFAULT '',
            registered_capital TEXT DEFAULT '', establishment_date TEXT DEFAULT '', acquisition_date TEXT DEFAULT '',
            registration_authority TEXT DEFAULT '', registration_number TEXT DEFAULT '',
            unified_social_credit_code TEXT DEFAULT '', tax_id TEXT UNIQUE,
            business_scope TEXT DEFAULT '', contributed_legal REAL DEFAULT 0.0,
            contributed_shareholder REAL DEFAULT 0.0, note TEXT DEFAULT '', source TEXT DEFAULT 'canonical_master',
            data_as_of TEXT DEFAULT '', created_at TEXT DEFAULT '', updated_at TEXT DEFAULT ''
        )
        """
    )
    existing = {row[1] for row in cur.execute("PRAGMA table_info(entities)").fetchall()}
    optional = {
        "entity_code": "TEXT",
        "business_role": "TEXT DEFAULT ''",
        "entity_kind": "TEXT DEFAULT 'company'",
        "legal_entity": "INTEGER DEFAULT 1",
        "parent_entity_code": "TEXT",
        "status": "TEXT DEFAULT 'active'",
        "tax_id": "TEXT",
    }
    for column, declaration in optional.items():
        if column not in existing:
            cur.execute(f"ALTER TABLE entities ADD COLUMN {column} {declaration}")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_entity_code ON entities(entity_code) WHERE entity_code IS NOT NULL")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_tax_id ON entities(tax_id) WHERE tax_id IS NOT NULL")


def import_via_sqlite(
    entities: Iterable[Mapping[str, Any]],
    db_path: str,
    dry_run: bool = False,
) -> tuple[int, int, int, list[tuple[str, int]]]:
    """Atomically insert/update the canonical cache by entity_code.

    Existing rows are matched only by canonical code (or tax id conflict
    detection).  Names are display attributes and are never identity keys.
    """
    rows = validate_master_entities(entities)
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    try:
        _ensure_schema(cur)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        created = updated = 0
        columns = [
            "entity_code", "name", "short_name", "entity_type", "industry", "business_role", "entity_kind",
            "legal_entity", "parent_entity_code", "status", "legal_representative", "legal_rep_id",
            "legal_rep_phone", "shareholders", "supervisor", "finance_officer", "registered_capital",
            "establishment_date", "acquisition_date", "registration_authority", "registration_number",
            "unified_social_credit_code", "tax_id", "business_scope", "note", "source", "data_as_of", "updated_at",
            "created_at",
        ]
        for row in rows:
            code = row["entity_code"]
            tax_id = _text(row.get("tax_id") or row.get("unified_social_credit_code"))
            existing = cur.execute("SELECT id, entity_code, tax_id FROM entities WHERE entity_code = ?", (code,)).fetchone()
            tax_match = cur.execute("SELECT id, entity_code, tax_id FROM entities WHERE tax_id = ?", (tax_id,)).fetchone()
            if tax_match and tax_match[1] != code:
                raise ValueError(f"tax_id conflict {tax_id}: existing {tax_match[1]}, incoming {code}")
            if existing is None:
                # The pre-canonical RAG cache has real company names but no
                # code/tax-id columns populated.  A unique exact name may be
                # used once to enrich that unresolved row; it is never used
                # to select among duplicate names or to overwrite a resolved
                # row.  This avoids inserting a second row into the legacy
                # UNIQUE(name) table while keeping name out of normal identity
                # matching.
                name_matches = cur.execute(
                    "SELECT id, entity_code, tax_id FROM entities WHERE name = ?", (row["name"],)
                ).fetchall()
                if len(name_matches) > 1:
                    raise ValueError(f"duplicate unresolved entity names: {row['name']}")
                if name_matches:
                    name_match = name_matches[0]
                    if name_match[1] and name_match[1] != code:
                        raise ValueError(f"entity name/code conflict {row['name']}: existing {name_match[1]}, incoming {code}")
                    if name_match[2] and name_match[2] != tax_id:
                        raise ValueError(f"entity name/tax_id conflict {row['name']}")
                    existing = name_match
            values = [
                row.get(column, None) for column in columns
            ]
            values[columns.index("legal_entity")] = 1 if row.get("legal_entity", True) else 0
            values[columns.index("tax_id")] = tax_id
            values[columns.index("updated_at")] = now
            values[columns.index("created_at")] = now
            if existing:
                set_clause = ", ".join(f"{column} = ?" for column in columns if column != "created_at")
                update_values = [values[columns.index(column)] for column in columns if column != "created_at"]
                # Match by id so this also updates an unresolved legacy row
                # whose entity_code was NULL.
                update_values.append(existing[0])
                cur.execute(f"UPDATE entities SET {set_clause} WHERE id = ?", update_values)
                updated += 1
            else:
                # ``created_at`` is written only on insert, preserving the
                # original creation timestamp during deterministic refreshes.
                table_columns = {row_info[1] for row_info in cur.execute("PRAGMA table_info(entities)").fetchall()}
                insert_columns = [column for column in columns if column in table_columns]
                placeholders = ", ".join("?" for _ in insert_columns)
                insert_values = [values[columns.index(column)] if column in columns else now for column in insert_columns]
                cur.execute(
                    f"INSERT INTO entities ({', '.join(insert_columns)}) VALUES ({placeholders})",
                    insert_values,
                )
                created += 1

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        rows_by_role = cur.execute(
            "SELECT business_role, COUNT(*) FROM entities GROUP BY business_role ORDER BY business_role"
        ).fetchall()
        total = sum(count for _, count in rows_by_role)
        return created, updated, total, rows_by_role
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _api_request(base_url: str, path: str, payload: Any = None) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the canonical 26-company RAG entity master")
    parser.add_argument("--master-file", required=True, help="JSON/CSV master file, or XLSX when openpyxl is installed")
    parser.add_argument("--dry-run", action="store_true", help="validate and show changes without committing")
    parser.add_argument("--api-url", default="", help="RAG service URL; omitted uses SQLite")
    parser.add_argument("--db-url", default="sqlite:///./data/entities.db", help="SQLite URL for local mode")
    args = parser.parse_args(argv)

    try:
        entities = extract_entities(args.master_file)
        rows = list(entities.values())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"validated {len(rows)} canonical entities")
    for row in sorted(rows, key=lambda item: item["entity_code"]):
        print(f"  {row['entity_code']} [{row['business_role']}] {row['name']} tax_id={row['tax_id']}")

    if args.api_url:
        payload = [{key: value for key, value in row.items() if key not in {"unified_social_credit_code"}}
                   for row in rows]
        if args.dry_run:
            print("dry-run: API write skipped")
            return 0
        try:
            result = _api_request(args.api_url, "/api/v1/entities/batch", payload)
        except Exception as exc:
            print(f"ERROR: API import failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0

    try:
        result = import_via_sqlite(rows, _sqlite_path(args.db_url), dry_run=args.dry_run)
    except Exception as exc:
        print(f"ERROR: SQLite import failed: {exc}", file=sys.stderr)
        return 1
    created, updated, total, by_role = result
    print(f"{'dry-run ' if args.dry_run else ''}completed: created={created}, updated={updated}, total={total}")
    print("roles:", ", ".join(f"{role or 'unclassified'}={count}" for role, count in by_role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
