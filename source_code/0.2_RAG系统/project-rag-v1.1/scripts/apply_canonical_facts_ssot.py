#!/usr/bin/env python3
"""Apply the idempotent Canonical Facts SSOT SQL migration."""
from pathlib import Path

from app.db import engine

SQL_PATH = Path(__file__).resolve().parents[1] / "migrations" / "20260901_canonical_facts_ssot.sql"


def _statements(sql: str) -> list[str]:
    """Split this DDL-only migration into driver-safe statements."""
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def main() -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in _statements(sql):
            conn.exec_driver_sql(statement)
    print("canonical facts SSOT migration applied")


if __name__ == "__main__":
    main()
