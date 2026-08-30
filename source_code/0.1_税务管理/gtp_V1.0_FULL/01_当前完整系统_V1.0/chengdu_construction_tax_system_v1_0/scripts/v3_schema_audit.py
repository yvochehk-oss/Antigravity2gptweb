#!/usr/bin/env python3
"""V3 Task 01 — read-only Tax + RAG shared-schema drift audit.

Tax and RAG deliberately share PostgreSQL ``public`` but have separate
SQLAlchemy registries and Alembic version tables. The audit merges both
metadata snapshots before classifying live objects; a one-sided Tax
``alembic check`` is not a valid Gate S0-01 implementation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = next(parent for parent in ROOT.parents if parent.name == "source_code")
RAG_ROOT = SOURCE_ROOT / "0.2_RAG系统" / "project-rag-v1.1"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("V3 schema audit is PostgreSQL-only")
    return value


def _norm(value: object) -> str:
    return re.sub(r"\s+", "", "" if value is None else str(value)).upper()


def _type_name(column_type, dialect) -> str:
    try:
        return _norm(column_type.compile(dialect=dialect))
    except Exception:
        return "NULL"


def _default_name(column) -> str | None:
    default = getattr(column, "server_default", None)
    if default is None:
        return None
    return _norm(getattr(default, "arg", default))


def _metadata_snapshot(metadata, dialect) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for table in metadata.sorted_tables:
        columns = {
            column.name: {
                "type": _type_name(column.type, dialect),
                "nullable": bool(column.nullable),
                "default": _default_name(column),
            }
            for column in table.columns
        }
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        uniques.update(
            tuple(column.name for column in index.columns)
            for index in table.indexes
            if index.unique
        )
        fks = {
            (
                tuple(column.name for column in constraint.columns),
                next(iter(constraint.elements)).column.table.name,
                tuple(element.column.name for element in constraint.elements),
            )
            for constraint in table.foreign_key_constraints
            if constraint.elements
        }
        tables[table.name] = {
            "columns": columns,
            "pk": [column.name for column in table.primary_key.columns],
            "uniques": [list(value) for value in sorted(uniques)],
            "fks": [
                [list(local), remote, list(remote_cols)]
                for local, remote, remote_cols in sorted(fks)
            ],
            "indexes": [
                [list(column.name for column in index.columns), bool(index.unique)]
                for index in sorted(table.indexes, key=lambda item: item.name or "")
                if not index.unique
            ],
            "checks": sorted(
                str(constraint.name or "")
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            ),
        }
    return {"tables": tables}


def _dump_rag_metadata() -> int:
    sys.path.insert(0, str(RAG_ROOT))
    os.environ.setdefault("PROJECT_RAG_DB_URL", _database_url())
    from ai_review import models as _ai_models  # noqa: F401
    from app import models as _rag_models  # noqa: F401
    from app.db import Base as RagBase

    snapshot = _metadata_snapshot(RagBase.metadata, postgresql.dialect())
    snapshot["alembic_heads"] = sorted(_disk_heads(RAG_ROOT))
    print(json.dumps(snapshot, ensure_ascii=False))
    return 0


def _load_rag_metadata(database_url: str) -> dict[str, Any]:
    if not RAG_ROOT.is_dir():
        raise RuntimeError(f"RAG source root not found: {RAG_ROOT}")
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PROJECT_RAG_DB_URL"] = database_url
    configured_python = os.getenv("V3_RAG_PYTHON", "").strip()
    bundled_python = RAG_ROOT / ".venv" / "bin" / "python"
    rag_python = configured_python or (str(bundled_python) if bundled_python.exists() else sys.executable)
    completed = subprocess.run(
        [rag_python, str(Path(__file__).resolve()), "--dump-rag-metadata"],
        cwd=RAG_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown error"
        raise RuntimeError(f"unable to load RAG SQLAlchemy metadata: {message}")
    return json.loads(completed.stdout)


def _disk_heads(root: Path) -> set[str]:
    config = Config(str(root / "alembic.ini"))
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        config.set_main_option("script_location", str(root / script_location))
    return set(ScriptDirectory.from_config(config).get_heads())


def _migration_text() -> str:
    chunks: list[str] = []
    for migration_root in (ROOT / "alembic" / "versions", RAG_ROOT / "alembic" / "versions"):
        for path in sorted(migration_root.glob("*.py")):
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    idp_schema = SOURCE_ROOT / "0.4_IDP文档录入引擎_V3.0" / "database" / "schema_v3.sql"
    if idp_schema.is_file():
        chunks.append(idp_schema.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _db_uniques(inspector, table_name: str) -> set[tuple[str, ...]]:
    result = {
        tuple(row.get("column_names") or [])
        for row in inspector.get_unique_constraints(table_name, schema="public")
    }
    result.update(
        tuple(row.get("column_names") or [])
        for row in inspector.get_indexes(table_name, schema="public")
        if row.get("unique")
    )
    return {value for value in result if value}


def _db_fks(inspector, table_name: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        (
            tuple(row.get("constrained_columns") or []),
            str(row.get("referred_table") or ""),
            tuple(row.get("referred_columns") or []),
        )
        for row in inspector.get_foreign_keys(table_name, schema="public")
    }


def _db_indexes(inspector, table_name: str) -> set[tuple[tuple[str, ...], bool]]:
    return {
        (tuple(row.get("column_names") or []), bool(row.get("unique")))
        for row in inspector.get_indexes(table_name, schema="public")
        if not row.get("unique") and row.get("column_names")
    }


def _db_checks(inspector, table_name: str) -> set[str]:
    return {
        str(row.get("name") or "")
        for row in inspector.get_check_constraints(table_name, schema="public")
    }


def _table_expected(owner_specs: list[dict[str, Any]]) -> dict[str, Any]:
    columns: dict[str, list[dict[str, Any]]] = {}
    for spec in owner_specs:
        for name, column in spec["columns"].items():
            columns.setdefault(name, []).append(column)
    return {
        "columns": columns,
        "pks": {tuple(spec["pk"]) for spec in owner_specs if spec["pk"]},
        "uniques": {tuple(value) for spec in owner_specs for value in spec["uniques"]},
        "fks": {
            (tuple(value[0]), value[1], tuple(value[2]))
            for spec in owner_specs
            for value in spec["fks"]
        },
        "indexes": {
            (tuple(value[0]), bool(value[1]))
            for spec in owner_specs
            for value in spec["indexes"]
        },
        "checks": {value for spec in owner_specs for value in spec["checks"] if value},
    }


def run(strict: bool = False) -> dict[str, Any]:
    database_url = _database_url()
    sys.path.insert(0, str(ROOT))
    from app import models as _tax_models  # noqa: F401
    from app.db import Base as TaxBase

    tax_snapshot = _metadata_snapshot(TaxBase.metadata, postgresql.dialect())
    rag_snapshot = _load_rag_metadata(database_url)
    owners: dict[str, list[str]] = {}
    specs: dict[str, list[dict[str, Any]]] = {}
    for owner, snapshot in (("tax", tax_snapshot), ("rag", rag_snapshot)):
        for table_name, table_spec in snapshot["tables"].items():
            owners.setdefault(table_name, []).append(owner)
            specs.setdefault(table_name, []).append(table_spec)

    failures: list[str] = []
    warnings: list[str] = []
    table_drift: dict[str, list[str]] = {}
    migration_text = _migration_text()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)

    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            inspector = inspect(conn)
            db_tables = set(inspector.get_table_names(schema="public"))
            model_tables = set(specs)
            missing_tables = sorted(model_tables - db_tables)
            raw_extra_tables = sorted(
                db_tables - model_tables - {"alembic_version_tax", "alembic_version_rag"}
            )
            explained_extra_tables = sorted(name for name in raw_extra_tables if name in migration_text)
            unexplained_extra_tables = sorted(set(raw_extra_tables) - set(explained_extra_tables))
            if missing_tables:
                failures.append(f"missing metadata-owned tables in DB: {missing_tables}")
            if unexplained_extra_tables:
                warnings.append(f"unexplained DB-only tables: {unexplained_extra_tables}")

            for table_name in sorted(model_tables & db_tables):
                expected = _table_expected(specs[table_name])
                drift: list[str] = []
                db_columns = {
                    row["name"]: row
                    for row in inspector.get_columns(table_name, schema="public")
                }
                expected_columns = set(expected["columns"])
                for name in sorted(expected_columns - set(db_columns)):
                    failures.append(f"{table_name}.{name}: missing in DB")
                    drift.append(f"missing column {name}")
                for name in sorted(set(db_columns) - expected_columns):
                    warnings.append(
                        f"{table_name}.{name}: DB-only column absent from Tax and RAG metadata"
                    )
                    drift.append(f"DB-only column {name}")

                for name in sorted(expected_columns & set(db_columns)):
                    db_column = db_columns[name]
                    candidates = expected["columns"][name]
                    allowed_types = {item["type"] for item in candidates}
                    allowed_nullable = {bool(item["nullable"]) for item in candidates}
                    db_type = _type_name(db_column["type"], engine.dialect)
                    if db_type == "NULL":
                        formatted_type = conn.execute(
                            text(
                                """
                                SELECT format_type(a.atttypid, a.atttypmod)
                                FROM pg_catalog.pg_attribute a
                                JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                                WHERE n.nspname='public' AND c.relname=:table_name
                                  AND a.attname=:column_name AND a.attnum>0
                                  AND NOT a.attisdropped
                                """
                            ),
                            {"table_name": table_name, "column_name": name},
                        ).scalar_one_or_none()
                        db_type = _norm(formatted_type)
                    if db_type not in allowed_types:
                        warnings.append(
                            f"{table_name}.{name}: type metadata={sorted(allowed_types)} db={db_type}"
                        )
                        drift.append(f"type {name}")
                    if bool(db_column["nullable"]) not in allowed_nullable:
                        failures.append(
                            f"{table_name}.{name}: nullable metadata={sorted(allowed_nullable)} "
                            f"db={db_column['nullable']}"
                        )
                        drift.append(f"nullable {name}")
                    if len(allowed_types) > 1 or len(allowed_nullable) > 1:
                        warnings.append(
                            f"{table_name}.{name}: Tax/RAG metadata disagree "
                            f"types={sorted(allowed_types)} nullable={sorted(allowed_nullable)}"
                        )
                        drift.append(f"cross-metadata {name}")

                db_pk = tuple(
                    (inspector.get_pk_constraint(table_name, schema="public") or {}).get(
                        "constrained_columns"
                    )
                    or []
                )
                if expected["pks"] and db_pk not in expected["pks"]:
                    failures.append(f"{table_name}: PK metadata={sorted(expected['pks'])} db={db_pk}")
                    drift.append("PK")

                comparisons = (
                    ("UNIQUE", expected["uniques"], _db_uniques(inspector, table_name)),
                    ("FK", expected["fks"], _db_fks(inspector, table_name)),
                    ("index", expected["indexes"], _db_indexes(inspector, table_name)),
                    ("CHECK", expected["checks"], _db_checks(inspector, table_name)),
                )
                for label, expected_values, db_values in comparisons:
                    missing_values = expected_values - db_values
                    extra_values = db_values - expected_values
                    if missing_values:
                        warnings.append(
                            f"{table_name}: missing {label} from metadata "
                            f"{sorted(missing_values, key=repr)}"
                        )
                        drift.append(f"missing {label}")
                    if extra_values:
                        warnings.append(
                            f"{table_name}: DB-only {label} {sorted(extra_values, key=repr)}"
                        )
                        drift.append(f"DB-only {label}")
                if drift:
                    table_drift[table_name] = sorted(set(drift))

            actual_tax_heads = {
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
                if row[0]
            }
            actual_rag_heads = {
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version_rag"))
                if row[0]
            }
            expected_tax_heads = _disk_heads(ROOT)
            expected_rag_heads = set(rag_snapshot["alembic_heads"])
            if actual_tax_heads != expected_tax_heads:
                failures.append(
                    f"Tax Alembic head mismatch: db={sorted(actual_tax_heads)} "
                    f"disk={sorted(expected_tax_heads)}"
                )
            if actual_rag_heads != expected_rag_heads:
                failures.append(
                    f"RAG Alembic head mismatch: db={sorted(actual_rag_heads)} "
                    f"disk={sorted(expected_rag_heads)}"
                )

            triggers = [
                dict(row._mapping)
                for row in conn.execute(
                    text(
                        """
                        SELECT DISTINCT event_object_table AS table_name, trigger_name
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
                        SELECT DISTINCT p.proname AS function_name
                        FROM pg_proc p
                        JOIN pg_namespace n ON n.oid=p.pronamespace
                        LEFT JOIN pg_depend d ON d.classid='pg_proc'::regclass
                          AND d.objid=p.oid AND d.deptype='e'
                        WHERE n.nspname='public' AND d.objid IS NULL
                        ORDER BY p.proname
                        """
                    )
                )
            ]
            unexplained_triggers = [
                row for row in triggers if row["trigger_name"] not in migration_text
            ]
            unexplained_functions = [
                row for row in functions if row["function_name"] not in migration_text
            ]
            if unexplained_triggers:
                warnings.append(f"unexplained public triggers: {unexplained_triggers}")
            if unexplained_functions:
                warnings.append(f"unexplained public functions: {unexplained_functions}")
        finally:
            conn.exec_driver_sql("ROLLBACK")
    engine.dispose()

    status = "FAIL" if failures or (strict and warnings) else ("WARNING" if warnings else "PASS")
    return {
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "details": {
            "metadata_table_count": len(model_tables),
            "tax_metadata_table_count": len(tax_snapshot["tables"]),
            "rag_metadata_table_count": len(rag_snapshot["tables"]),
            "shared_tables": sorted(name for name, value in owners.items() if len(value) > 1),
            "db_table_count": len(db_tables),
            "missing_tables": missing_tables,
            "explained_db_only_tables": explained_extra_tables,
            "unexplained_db_only_tables": unexplained_extra_tables,
            "tax_alembic_db_heads": sorted(actual_tax_heads),
            "tax_alembic_disk_heads": sorted(expected_tax_heads),
            "rag_alembic_db_heads": sorted(actual_rag_heads),
            "rag_alembic_disk_heads": sorted(expected_rag_heads),
            "triggers": triggers,
            "functions": functions,
            "table_drift": table_drift,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="treat warnings as Gate failures")
    parser.add_argument("--json", dest="json_path", help="write audit JSON")
    parser.add_argument("--dump-rag-metadata", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.dump_rag_metadata:
        return _dump_rag_metadata()
    result = run(strict=args.strict)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
