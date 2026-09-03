#!/usr/bin/env python3
"""FVAT-2 deterministic command for one Formal VAT statutory resource."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services.formal_vat_rebuild import (  # noqa: E402
    make_formal_vat_rebuild_plan,
    rebuild_formal_vat_statutory_resource,
)


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Formal VAT statutory rebuild is PostgreSQL-only")
    return value


def _write_result(payload, path: str | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entity", required=True)
    parser.add_argument("--period", required=True, help="YYYY-MM")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restatement", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--created-by", default="fvat-2-cli")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    database_url = _database_url()
    database_name = str(make_url(database_url).database or "")
    engine = create_engine(database_url, future=True, pool_pre_ping=True)

    with Session(engine) as session:
        plan = make_formal_vat_rebuild_plan(
            session,
            entity_code=args.entity,
            period=args.period,
        )
        if not args.apply:
            result = {
                "kind": "V3_FORMAL_VAT_STATUTORY_REBUILD_PLAN_RESULT",
                "database": database_name,
                **plan,
            }
            _write_result(result, args.json_path)
            return 0

        if args.confirm_database != database_name:
            raise SystemExit(
                "--apply requires --confirm-database matching the connected database"
            )

        result = rebuild_formal_vat_statutory_resource(
            session,
            entity_code=args.entity,
            period=args.period,
            created_by=args.created_by,
            allow_restatement=args.restatement,
            expected_input_snapshot_sha256=plan["input_snapshot_sha256"],
        )
        session.commit()
        payload = {
            "kind": "V3_FORMAL_VAT_STATUTORY_REBUILD_RESULT",
            "database": database_name,
            "plan_digest": plan["plan_digest"],
            **result,
        }
        _write_result(payload, args.json_path)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
