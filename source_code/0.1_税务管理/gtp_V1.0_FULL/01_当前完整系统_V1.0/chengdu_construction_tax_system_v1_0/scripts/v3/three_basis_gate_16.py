#!/usr/bin/env python3
"""Read-only Gate S16 verifier for TAX / ACCRUAL / CASH separation."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.calc.basis.cash_basis import LEGACY_CASHFLOW_FALLBACK_ALLOWED
from app.calc.basis.contracts import (
    BasisSource,
    CalculationBasis,
    SourceKind,
    SourceRole,
    is_source_allowed,
)

ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S16 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    config = Config(str(ROOT / "alembic.ini"))
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        config.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(config).get_heads())


def run() -> dict[str, Any]:
    failures: list[str] = []
    evidence: dict[str, Any] = {}

    payment_primary = BasisSource(SourceKind.PAYMENT_FACT, SourceRole.PRIMARY_AMOUNT)
    invoice_primary = BasisSource(SourceKind.INVOICE_FACT, SourceRole.PRIMARY_AMOUNT)
    invoice_support = BasisSource(SourceKind.INVOICE_FACT, SourceRole.SUPPORTING_EVIDENCE)
    invoice_period = BasisSource(SourceKind.INVOICE_FACT, SourceRole.PERIOD_ATTRIBUTION)
    claim_period = BasisSource(SourceKind.INPUT_VAT_CLAIM, SourceRole.PERIOD_ATTRIBUTION)

    checks = {
        "payment_is_not_cost": not is_source_allowed(CalculationBasis.ACCRUAL, payment_primary),
        "invoice_is_not_revenue_recognition": not is_source_allowed(CalculationBasis.ACCRUAL, invoice_primary),
        "invoice_supporting_evidence_allowed": is_source_allowed(CalculationBasis.ACCRUAL, invoice_support),
        "invoice_date_is_not_input_vat_period": (
            not is_source_allowed(CalculationBasis.TAX, invoice_period)
            and is_source_allowed(CalculationBasis.TAX, claim_period)
        ),
        "cash_uses_payment_fact": is_source_allowed(CalculationBasis.CASH, payment_primary),
        "legacy_cashflow_fallback_disabled": LEGACY_CASHFLOW_FALLBACK_ALLOWED is False,
    }
    for name, passed in checks.items():
        if not passed:
            failures.append(f"Task16 basis contract failed: {name}")

    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names(schema="public"))
        db_heads = {str(row[0]) for row in conn.execute(text("SELECT version_num FROM alembic_version_tax")) if row[0]}
        disk_heads = _disk_heads()
        if db_heads != disk_heads:
            failures.append(f"formal DB/disk Alembic heads differ: db={sorted(db_heads)} disk={sorted(disk_heads)}")

        claim_columns: set[str] = set()
        if "input_vat_claims" in tables:
            claim_columns = {row["name"] for row in inspector.get_columns("input_vat_claims", schema="public")}
        if "claim_period" not in claim_columns:
            failures.append("input_vat_claims.claim_period is required for TAX period attribution")

        prohibited_columns: list[str] = []
        if "entity_vat_ledgers" in tables:
            ledger_columns = {row["name"] for row in inspector.get_columns("entity_vat_ledgers", schema="public")}
            prohibited_columns = sorted(
                ledger_columns & {"project_id", "invoice_date", "payment_date", "recognized_revenue", "cost_amount", "cashflow_id"}
            )
            if prohibited_columns:
                failures.append(f"entity_vat_ledgers contains mixed-axis columns: {prohibited_columns}")

        evidence.update(
            {
                "database": make_url(database_url).database,
                "alembic_db_heads": sorted(db_heads),
                "alembic_disk_heads": sorted(disk_heads),
                "input_vat_claim_period_present": "claim_period" in claim_columns,
                "entity_vat_ledger_prohibited_columns": prohibited_columns,
            }
        )

    evidence.update(checks)
    evidence["task16_schema_migration_required"] = False
    return {"gate": "S16", "status": "PASS" if not failures else "FAIL", "failures": failures, "evidence": evidence}


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
