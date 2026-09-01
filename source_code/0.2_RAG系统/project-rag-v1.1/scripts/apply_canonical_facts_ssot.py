#!/usr/bin/env python3
"""Apply the idempotent Canonical Facts SSOT SQL migration."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db import engine

SQL_PATHS = (
    PROJECT_ROOT / "migrations" / "20260901_canonical_facts_ssot.sql",
    PROJECT_ROOT / "migrations" / "20260902_canonical_facts_phase25_hardening.sql",
)


def _statements(sql: str) -> list[str]:
    """Split this DDL-only migration into driver-safe statements."""
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def main() -> None:
    with engine.begin() as conn:
        for sql_path in SQL_PATHS:
            sql = sql_path.read_text(encoding="utf-8")
            for statement in _statements(sql):
                conn.exec_driver_sql(statement)
    print(f"canonical facts SSOT migrations applied: {len(SQL_PATHS)}")


if __name__ == "__main__":
    main()
