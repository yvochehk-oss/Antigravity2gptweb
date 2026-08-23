#!/usr/bin/env python3
"""Safely remove explicitly identified stability-test database residue.

This command is intentionally narrow.  It accepts only the two project codes
created by ``tests/test_stability_observability.py`` and verifies their test
markers before deleting dependent rows and projects in one transaction.  It
does not remove files, canonical entities, external parties, or any other
project.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_TEST_PROJECTS = {
    "RETENTION-001": ("稳定性测试项目", "construction-tax"),
    "RETENTION-002": ("稳定性测试项目", "construction-tax"),
}


class ResiduePurgeError(RuntimeError):
    """Raised when the explicit test-residue proof does not match."""


def _backup_sqlite(database: Path, backup_dir: Path) -> Path:
    if not database.is_file():
        raise ResiduePurgeError(f"runtime database not found: {database}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{database.stem}.pre-test-residue-purge-{stamp}.db"
    suffix = 1
    while destination.exists():
        destination = backup_dir / (
            f"{database.stem}.pre-test-residue-purge-{stamp}-{suffix}.db"
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


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]


def _dependent_project_tables(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    """Find application tables with a project_id column, without hard-coding only known models."""
    result: list[tuple[str, str]] = []
    for table in _table_names(connection):
        if table in {"projects", "alembic_version"}:
            continue
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if "project_id" in columns:
            result.append((table, "project_id"))
    return result


def purge_test_residue(
    runtime_path: str | Path,
    *,
    project_codes: list[str],
    backup_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    runtime = Path(runtime_path).expanduser().resolve()
    requested = list(dict.fromkeys(project_codes))
    if not requested or any(code not in ALLOWED_TEST_PROJECTS for code in requested):
        raise ResiduePurgeError(
            f"only explicit stability-test codes are allowed: {sorted(ALLOWED_TEST_PROJECTS)}"
        )
    backup = _backup_sqlite(
        runtime,
        Path(backup_dir).expanduser().resolve() if backup_dir else runtime.parent / "backups",
    )
    connection = sqlite3.connect(str(runtime))
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        rows = []
        for code in requested:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_code = ?", (code,)
            ).fetchone()
            if row is None:
                raise ResiduePurgeError(f"expected test project is missing: {code}")
            expected_name, expected_system = ALLOWED_TEST_PROJECTS[code]
            if row["name"] != expected_name or row["external_system"] != expected_system:
                raise ResiduePurgeError(
                    f"project marker mismatch for {code}: "
                    f"name={row['name']!r}, external_system={row['external_system']!r}"
                )
            rows.append(row)
        ids = [int(row["id"]) for row in rows]
        connection.execute("BEGIN IMMEDIATE")
        deleted_dependents: dict[str, int] = {}
        for table, column in _dependent_project_tables(connection):
            quoted_table = '"' + table.replace('"', '""') + '"'
            placeholders = ", ".join("?" for _ in ids)
            count = connection.execute(
                f"SELECT COUNT(*) FROM {quoted_table} WHERE {column} IN ({placeholders})",
                ids,
            ).fetchone()[0]
            if count:
                connection.execute(
                    f"DELETE FROM {quoted_table} WHERE {column} IN ({placeholders})",
                    ids,
                )
                deleted_dependents[table] = int(count)
        placeholders = ", ".join("?" for _ in requested)
        connection.execute(
            f"DELETE FROM projects WHERE project_code IN ({placeholders})", requested
        )
        remaining = connection.execute(
            f"SELECT COUNT(*) FROM projects WHERE project_code IN ({placeholders})",
            requested,
        ).fetchone()[0]
        if remaining:
            raise ResiduePurgeError("test residue remained after purge")
        report = {
            "runtime_path": str(runtime),
            "backup_path": str(backup),
            "project_codes": requested,
            "deleted_projects": len(rows),
            "deleted_dependents": deleted_dependents,
            "dry_run": dry_run,
            "committed": False,
        }
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
    parser = argparse.ArgumentParser(description="Purge only verified stability-test residue")
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--project-code", action="append", required=True)
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = purge_test_residue(
            args.runtime,
            project_codes=args.project_code,
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
