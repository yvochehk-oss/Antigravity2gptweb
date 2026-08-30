#!/usr/bin/env python3
"""V3 Task 01 — PostgreSQL schema drift audit.

Read-only comparison of live PostgreSQL, SQLAlchemy metadata and Alembic heads.
The normal mode distinguishes hard structural failures from reviewable drift;
``--strict`` promotes every warning to a blocking Gate S0-01 failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import Base  # noqa: E402
import app.models  # noqa: F401,E402


def _url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    url = make_url(value)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("V3 schema audit is PostgreSQL-only")
    return value


def _model_type(column, dialect) -> str:
    return column.type.compile(dialect=dialect).upper().replace(" ", "")


def _db_type(column) -> str:
    return str(column["type"]).upper().replace(" ", "")


def _model_uniques(table) -> set[tuple[str, ...]]:
    return {
        tuple(sorted(col.name for col in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _db_uniques(inspector, table_name: str) -> set[tuple[str, ...]]:
    return {
        tuple(sorted(row.get("column_names") or []))
        for row in inspector.get_unique_constraints(table_name, schema="public")
    }


def _model_fks(table) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    result = set()
    for constraint in table.foreign_key_constraints:
        local = tuple(col.name for col in constraint.columns)
        elements = list(constraint.elements)
        if not elements:
            continue
        remote_table = elements[0].column.table.name
        remote_cols = tuple(element.column.name for element in elements)
        result.add((local, remote_table, remote_cols))
    return result


def _db_fks(inspector, table_name: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    result = set()
    for row in inspector.get_foreign_keys(table_name, schema="public"):
        result.add(
            (
                tuple(row.get("constrained_columns") or []),
                str(row.get("referred_table") or ""),
                tuple(row.get("referred_columns") or []),
            )
        )
    return result


def _model_indexes(table) -> set[tuple[str, tuple[str, ...], bool]]:
    return {
        (
            str(index.name or ""),
            tuple(col.name for col in index.columns),
            bool(index.unique),
        )
        for index in table.indexes
    }


def _db_indexes(inspector, table_name: str) -> set[tuple[str, tuple[str, ...], bool]]:
    return {
        (
            str(row.get("name") or ""),
            tuple(row.get("column_names") or []),
            bool(row.get("unique")),
        )
        for row in inspector.get_indexes(table_name, schema="public")
    }


def _model_checks(table) -> set[str]:
    return {
        str(constraint.name or "")
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _db_checks(inspector, table_name: str) -> set[str]:
    return {
        str(row.get("name") or "")
        for row in inspector.get_check_constraints(table_name, schema="public")
    }


def run(strict: bool = False) -> dict:
    engine = create_engine(_url(), future=True, pool_pre_ping=True)
    inspector = inspect(engine)
    model_tables = set(Base.metadata.tables)
    db_tables = set(inspector.get_table_names(schema="public"))

    failures: list[str] = []
    warnings: list[str] = []
    table_details: dict[str, object] = {}

    missing_tables = sorted(model_tables - db_tables)
    extra_tables = sorted(db_tables - model_tables - {"alembic_version_tax"})
    if missing_tables:
        failures.append(f"missing model tables in DB: {missing_tables}")
    if extra_tables:
        warnings.append(f"DB-only tables require explanation: {extra_tables}")

    for table_name in sorted(model_tables & db_tables):
        table = Base.metadata.tables[table_name]
        db_cols = {c["name"]: c for c in inspector.get_columns(table_name, schema="public")}
        model_cols = {c.name: c for c in table.columns}
        drift: list[str] = []

        for name in sorted(model_cols.keys() - db_cols.keys()):
            failures.append(f"{table_name}.{name}: missing in DB")
        for name in sorted(db_cols.keys() - model_cols.keys()):
            warnings.append(f"{table_name}.{name}: DB-only column")

        for name in sorted(model_cols.keys() & db_cols.keys()):
            model_col = model_cols[name]
            db_col = db_cols[name]
            model_type = _model_type(model_col, engine.dialect)
            db_type = _db_type(db_col)
            if model_type != db_type:
                drift.append(f"{name}: type model={model_type} db={db_type}")
            if bool(model_col.nullable) != bool(db_col["nullable"]):
                failures.append(
                    f"{table_name}.{name}: nullable model={model_col.nullable} db={db_col['nullable']}"
                )
        for item in drift:
            warnings.append(f"{table_name}.{item}")

        model_uniques = _model_uniques(table)
        db_uniques = _db_uniques(inspector, table_name)
        if model_uniques != db_uniques:
            warnings.append(
                f"{table_name}: UNIQUE drift model={sorted(model_uniques)} db={sorted(db_uniques)}"
            )

        model_fks = _model_fks(table)
        db_fks = _db_fks(inspector, table_name)
        if model_fks != db_fks:
            warnings.append(f"{table_name}: FK drift model={sorted(model_fks)} db={sorted(db_fks)}")

        model_indexes = _model_indexes(table)
        db_indexes = _db_indexes(inspector, table_name)
        if model_indexes != db_indexes:
            warnings.append(
                f"{table_name}: index drift model={sorted(model_indexes)} db={sorted(db_indexes)}"
            )

        model_checks = _model_checks(table)
        db_checks = _db_checks(inspector, table_name)
        if model_checks != db_checks:
            warnings.append(
                f"{table_name}: CHECK drift model={sorted(model_checks)} db={sorted(db_checks)}"
            )

        table_details[table_name] = {
            "model_columns": sorted(model_cols),
            "db_columns": sorted(db_cols),
            "model_uniques": sorted(model_uniques),
            "db_uniques": sorted(db_uniques),
            "model_fks": sorted(model_fks),
            "db_fks": sorted(db_fks),
            "model_indexes": sorted(model_indexes),
            "db_indexes": sorted(db_indexes),
            "model_checks": sorted(model_checks),
            "db_checks": sorted(db_checks),
        }

    cfg = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    expected_heads = set(script.get_heads())
    with engine.connect() as conn:
        actual_heads = {
            str(row[0])
            for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
            if row[0]
        }
        triggers = [
            dict(row._mapping)
            for row in conn.execute(
                text(
                    """
                    SELECT event_object_table AS table_name, trigger_name
                    FROM information_schema.triggers
                    WHERE trigger_schema='public'
                    ORDER BY event_object_table, trigger_name
                    """
                )
            )
        ]
        functions = [
            dict(row._mapping)
            for row in conn.execute(
                text(
                    """
                    SELECT p.proname AS function_name
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public'
                    ORDER BY p.proname
                    """
                )
            )
        ]

    if actual_heads != expected_heads:
        failures.append(
            f"Alembic head mismatch: db={sorted(actual_heads)} disk={sorted(expected_heads)}"
        )
    if triggers:
        warnings.append(f"public triggers require explanation: {len(triggers)}")
    if functions:
        warnings.append(f"public functions require explanation: {len(functions)}")

    engine.dispose()
    status = "FAIL" if failures or (strict and warnings) else ("WARNING" if warnings else "PASS")
    return {
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "details": {
            "model_table_count": len(model_tables),
            "db_table_count": len(db_tables),
            "missing_tables": missing_tables,
            "extra_tables": extra_tables,
            "alembic_db_heads": sorted(actual_heads),
            "alembic_disk_heads": sorted(expected_heads),
            "triggers": triggers,
            "functions": functions,
            "tables": table_details,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="treat warnings as Gate failures")
    parser.add_argument("--json", dest="json_path", help="write audit JSON")
    args = parser.parse_args()
    result = run(strict=args.strict)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
