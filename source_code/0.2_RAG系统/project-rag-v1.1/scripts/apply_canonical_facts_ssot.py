#!/usr/bin/env python3
"""Apply the idempotent Canonical Facts SSOT SQL migration."""
from pathlib import Path

from app.db import engine

SQL_PATH = Path(__file__).resolve().parents[1] / "migrations" / "20260901_canonical_facts_ssot.sql"


def main() -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)
    print("canonical facts SSOT migration applied")


if __name__ == "__main__":
    main()
