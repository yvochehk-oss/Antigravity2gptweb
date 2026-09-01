#!/usr/bin/env python3
"""Read-only Gate S10 verifier for Input VAT Claims and one entity-month pilot."""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.domain.party.resolver import PartyResolutionError  # noqa: E402
from scripts.v3.input_vat_pilot import (  # noqa: E402
    EXPECTED_HEAD,
    RESULT_KIND,
    SOURCE_SYSTEM,
    _load_profiles,
    _parse_period,
    _stable_reporting_party,
)

MONEY_TOLERANCE = Decimal("0.01")


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S10 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    script_location = Path(cfg.get_main_option("script_location"))
    if not script_location.is_absolute():
        cfg.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _read_result(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Task 10 result must be a JSON object")
    if payload.get("kind") != RESULT_KIND or int(payload.get("version", 0)) != 1:
        raise RuntimeError("unsupported Task 10 result format")
    return payload


def run(result_path: str) -> dict[str, Any]:
    result = _read_result(result_path)
    failures: list[str] = []
    evidence: dict[str, Any] = {}

    entity_code = str(result.get("entity_code") or "")
    period = str(result.get("period") or "")
    reporting_party_id = int(result.get("reporting_party_id") or 0)
    claim_ids = sorted({int(value) for value in result.get("claim_ids", [])})
    external_ids = [str(value) for value in result.get("source_external_claim_ids", [])]
    if not entity_code or not period or not reporting_party_id or not claim_ids:
        failures.append("Task 10 result is missing entity/period/reporting_party/claim_ids")

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            database = str(conn.execute(text("SELECT current_database()" )).scalar_one())
            db_heads = {
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
                if row[0]
            }
            disk_heads = _disk_heads()
            evidence.update(
                {
                    "database": database,
                    "alembic_db_heads": sorted(db_heads),
                    "alembic_disk_heads": sorted(disk_heads),
                    "entity_code": entity_code,
                    "period": period,
                    "reporting_party_id": reporting_party_id,
                    "claim_ids": claim_ids,
                }
            )
            if result.get("database") != database:
                failures.append(
                    f"result database mismatch: result={result.get('database')!r} actual={database!r}"
                )
            if db_heads != disk_heads or disk_heads != {EXPECTED_HEAD}:
                failures.append(
                    f"Task10 head mismatch: db={sorted(db_heads)} disk={sorted(disk_heads)}"
                )

            prohibited_columns = {
                str(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema='public'
                          AND table_name='input_vat_claims'
                          AND column_name IN ('project_id','invoice_date','deductible','legacy_period')
                        """
                    )
                )
            }
            evidence["prohibited_claim_columns"] = sorted(prohibited_columns)
            if prohibited_columns:
                failures.append(f"Input VAT Claim mixes fact/project fields: {sorted(prohibited_columns)}")

            selected_claims = []
            if claim_ids:
                stmt = text(
                    """
                    SELECT id, invoice_fact_id, reporting_party_id, claim_period,
                           claim_amount, event_type, claim_status, evidence_type,
                           confidence, source_system, external_claim_id
                    FROM input_vat_claims
                    WHERE id IN :claim_ids
                    ORDER BY id
                    """
                ).bindparams(bindparam("claim_ids", expanding=True))
                selected_claims = [dict(row) for row in conn.execute(stmt, {"claim_ids": claim_ids}).mappings()]
            if len(selected_claims) != len(claim_ids):
                failures.append(
                    f"selected claim coverage mismatch: db={len(selected_claims)} result={len(claim_ids)}"
                )

            target_period = _parse_period(period)
            selected_review_total = Decimal("0")
            selected_confirmed_total = Decimal("0")
            for claim in selected_claims:
                if claim["reporting_party_id"] != reporting_party_id:
                    failures.append(f"claim {claim['id']}: reporting party mismatch")
                if claim["claim_period"] != target_period:
                    failures.append(f"claim {claim['id']}: claim_period mismatch")
                if claim["source_system"] != SOURCE_SYSTEM:
                    failures.append(f"claim {claim['id']}: unexpected source_system")
                if claim["evidence_type"] != "LEGACY_ASSUMPTION":
                    failures.append(f"claim {claim['id']}: pilot evidence_type changed")
                if claim["confidence"] != "LOW":
                    failures.append(f"claim {claim['id']}: pilot confidence changed")
                if claim["claim_status"] != "NEEDS_REVIEW":
                    failures.append(f"claim {claim['id']}: pilot status must remain NEEDS_REVIEW")
                if claim["claim_status"] == "NEEDS_REVIEW":
                    selected_review_total += _money(claim["claim_amount"])
                elif claim["claim_status"] == "CONFIRMED":
                    selected_confirmed_total += _money(claim["claim_amount"])

            legacy_rows = [
                dict(row)
                for row in conn.execute(
                    text(
                        """
                        SELECT i.id, i.vat, m.invoice_fact_id
                        FROM invoices i
                        LEFT JOIN legacy_invoice_map m ON m.legacy_invoice_id=i.id
                        WHERE i.entity_code=:entity_code
                          AND i.period=:period
                          AND lower(i.direction)='in'
                          AND i.deductible
                          AND i.vat <> 0
                        ORDER BY i.id
                        """
                    ),
                    {"entity_code": entity_code, "period": period},
                ).mappings()
            ]
            legacy_total = sum((_money(row["vat"]) for row in legacy_rows), Decimal("0")).quantize(Decimal("0.01"))
            mapped_count = sum(1 for row in legacy_rows if row["invoice_fact_id"] is not None)
            residual = (legacy_total - selected_review_total - selected_confirmed_total).quantize(Decimal("0.01"))
            evidence.update(
                {
                    "legacy_candidate_count": len(legacy_rows),
                    "mapped_candidate_count": mapped_count,
                    "legacy_candidate_vat_total": str(legacy_total),
                    "selected_review_candidate_vat_total": str(selected_review_total.quantize(Decimal('0.01'))),
                    "selected_confirmed_linked_vat_total": str(selected_confirmed_total.quantize(Decimal('0.01'))),
                    "explained_residual": str(residual),
                }
            )
            if not legacy_rows:
                failures.append("selected entity/month has no legacy input VAT candidates")
            if mapped_count != len(legacy_rows):
                failures.append(f"legacy map coverage incomplete: {mapped_count}/{len(legacy_rows)}")
            if len(selected_claims) != len(legacy_rows):
                failures.append(
                    f"pilot claim coverage incomplete: claims={len(selected_claims)} legacy={len(legacy_rows)}"
                )
            if abs(residual) > MONEY_TOLERANCE:
                failures.append(f"entity-month VAT reconciliation residual={residual}")

            legacy_assumption_confirmed_count = int(
                conn.execute(
                    text(
                        """
                        SELECT count(*) FROM input_vat_claims
                        WHERE evidence_type='LEGACY_ASSUMPTION'
                          AND claim_status='CONFIRMED'
                        """
                    )
                ).scalar_one()
            )
            confirmed_nonvalid_invoice_count = int(
                conn.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM input_vat_claims c
                        JOIN facts f ON f.id=c.invoice_fact_id
                        WHERE c.claim_status='CONFIRMED'
                          AND (NOT f.is_current OR f.validation_status <> 'VALID')
                        """
                    )
                ).scalar_one()
            )
            confirmed_exceeds_invoice_vat_count = int(
                conn.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM (
                            SELECT c.invoice_fact_id,
                                   sum(c.claim_amount) AS confirmed_total,
                                   max(i.vat_amount) AS invoice_vat
                            FROM input_vat_claims c
                            JOIN invoice_facts i ON i.fact_id=c.invoice_fact_id
                            WHERE c.claim_status='CONFIRMED'
                            GROUP BY c.invoice_fact_id
                        ) x
                        WHERE x.invoice_vat IS NULL
                           OR abs(x.confirmed_total) > abs(x.invoice_vat) + 0.01
                        """
                    )
                ).scalar_one()
            )
            evidence["legacy_assumption_confirmed_count"] = legacy_assumption_confirmed_count
            evidence["confirmed_nonvalid_invoice_count"] = confirmed_nonvalid_invoice_count
            evidence["confirmed_exceeds_invoice_vat_count"] = confirmed_exceeds_invoice_vat_count
            if legacy_assumption_confirmed_count:
                failures.append(f"LEGACY_ASSUMPTION confirmed rows={legacy_assumption_confirmed_count}")
            if confirmed_nonvalid_invoice_count:
                failures.append(f"confirmed claims on non-VALID Invoice Facts={confirmed_nonvalid_invoice_count}")
            if confirmed_exceeds_invoice_vat_count:
                failures.append(f"confirmed claim totals exceed Invoice VAT={confirmed_exceeds_invoice_vat_count}")

            profiles = _load_profiles(conn)
            reporting_mismatch_ids: list[int] = []
            confirmed_rows = [
                dict(row)
                for row in conn.execute(
                    text(
                        """
                        SELECT c.id, c.reporting_party_id, c.claim_period, i.buyer_party_id
                        FROM input_vat_claims c
                        JOIN invoice_facts i ON i.fact_id=c.invoice_fact_id
                        WHERE c.claim_status='CONFIRMED'
                        ORDER BY c.id
                        """
                    )
                ).mappings()
            ]
            for row in confirmed_rows:
                if row["buyer_party_id"] is None:
                    reporting_mismatch_ids.append(int(row["id"]))
                    continue
                try:
                    expected_reporting = _stable_reporting_party(
                        int(row["buyer_party_id"]),
                        row["claim_period"],
                        profiles,
                    )
                except PartyResolutionError:
                    reporting_mismatch_ids.append(int(row["id"]))
                    continue
                if expected_reporting != int(row["reporting_party_id"]):
                    reporting_mismatch_ids.append(int(row["id"]))
            evidence["confirmed_reporting_mismatch_ids"] = reporting_mismatch_ids
            if reporting_mismatch_ids:
                failures.append(f"confirmed claim reporting-party mismatch ids={reporting_mismatch_ids}")

            reporting_period_confirmed_total = _money(
                conn.execute(
                    text(
                        """
                        SELECT coalesce(sum(claim_amount), 0)
                        FROM input_vat_claims
                        WHERE reporting_party_id=:reporting_party_id
                          AND claim_period=:claim_period
                          AND claim_status='CONFIRMED'
                        """
                    ),
                    {
                        "reporting_party_id": reporting_party_id,
                        "claim_period": target_period,
                    },
                ).scalar_one()
            )
            evidence["reporting_period_confirmed_input_vat"] = str(reporting_period_confirmed_total)
            if _money(result.get("reporting_period_confirmed_input_vat")) != reporting_period_confirmed_total:
                failures.append("reporting-period confirmed Input VAT changed since APPLY")

            if external_ids:
                db_external_ids = {
                    str(row[0])
                    for row in conn.execute(
                        text(
                            """
                            SELECT external_claim_id
                            FROM input_vat_claims
                            WHERE source_system=:source_system
                              AND external_claim_id = ANY(:external_ids)
                            """
                        ),
                        {"source_system": SOURCE_SYSTEM, "external_ids": external_ids},
                    )
                }
                if db_external_ids != set(external_ids):
                    failures.append("source external claim identity coverage mismatch")
        finally:
            conn.rollback()
    engine.dispose()

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    result = run(args.result)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.json_path:
        Path(args.json_path).write_text(payload + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
