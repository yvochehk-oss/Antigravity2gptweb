#!/usr/bin/env python3
"""Task18 canonical group penetration PLAN/APPLY builder."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.calc.penetration.group import (
    RULESET_VERSION,
    accrual_snapshot,
    canonical_hash,
    month_start,
    require_cash_penetration,
    summarize_accrual,
    summarize_tax,
    tax_snapshot,
)
from app.v3_group_models import GroupPenetrationComponent, GroupPenetrationResult
from app.v3_party_models import InternalEntity
from app.v3_period_models import CalculationRun

EXPECTED_HEAD = "89_v3_group_penetration"
PLAN_KIND = "V3_TASK18_GROUP_PENETRATION_PLAN"
RUN_TYPE = {"ACCRUAL": "GROUP_ACCRUAL", "TAX": "GROUP_TAX", "CASH": "GROUP_CASH"}


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task18 Group Penetration is PostgreSQL-only")
    return value


def _head(session: Session) -> str:
    return str(session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one())


def _anchor(session: Session, entity_code: str) -> InternalEntity:
    row = session.scalar(select(InternalEntity).where(InternalEntity.canonical_code == entity_code))
    if row is None:
        raise ValueError(f"unknown internal entity: {entity_code}")
    if not row.legal_entity:
        raise ValueError("group analysis anchor must be a legal internal entity")
    return row


def _result_from_snapshot(basis: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    if basis == "ACCRUAL":
        values = summarize_accrual(snapshot)
        return {
            "basis": basis,
            "status": "READY",
            "cycle_detected": False,
            "max_depth": int(snapshot["max_depth"]),
            **{key: str(value) for key, value in values.items()},
        }
    if basis == "TAX":
        values = summarize_tax(snapshot)
        return {
            "basis": basis,
            "status": "READY",
            "cycle_detected": False,
            "max_depth": 0,
            **{key: str(value) for key, value in values.items()},
        }
    require_cash_penetration()
    raise AssertionError("unreachable")


def make_plan(session: Session, *, anchor_entity: str, period: str | date, basis: str) -> dict[str, Any]:
    if _head(session) != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    basis = basis.strip().upper()
    if basis not in RUN_TYPE:
        raise ValueError("basis must be ACCRUAL, TAX or CASH")
    anchor = _anchor(session, anchor_entity)
    analysis_period = month_start(period)

    if basis == "CASH":
        blocker = "canonical PaymentFact is not available until Task19; legacy cashflows fallback is prohibited"
        core = {
            "kind": PLAN_KIND,
            "version": 1,
            "anchor_entity": anchor_entity,
            "anchor_party_id": anchor.party_id,
            "analysis_period": str(analysis_period),
            "basis": basis,
            "run_type": RUN_TYPE[basis],
            "ruleset_version": RULESET_VERSION,
            "status": "NOT_READY",
            "blocker": blocker,
        }
        return {**core, "plan_digest": canonical_hash(core)}

    snapshot = accrual_snapshot(session, period=analysis_period) if basis == "ACCRUAL" else tax_snapshot(session, period=analysis_period)
    result = _result_from_snapshot(basis, snapshot)
    input_hash = canonical_hash(snapshot)
    result_payload = {
        "anchor_party_id": anchor.party_id,
        "analysis_period": str(analysis_period),
        "input_snapshot_sha256": input_hash,
        **result,
    }
    result_hash = canonical_hash(result_payload)
    core = {
        "kind": PLAN_KIND,
        "version": 1,
        "anchor_entity": anchor_entity,
        "anchor_party_id": anchor.party_id,
        "analysis_period": str(analysis_period),
        "basis": basis,
        "run_type": RUN_TYPE[basis],
        "ruleset_version": RULESET_VERSION,
        "status": "READY",
        "input_snapshot_sha256": input_hash,
        "result_sha256": result_hash,
        "source_snapshot": snapshot,
        "result": result,
    }
    return {**core, "plan_digest": canonical_hash(core)}


def _latest_successful_run(session: Session, *, anchor_party_id: int, analysis_period: date, run_type: str) -> CalculationRun | None:
    return session.scalar(
        select(CalculationRun)
        .where(
            CalculationRun.reporting_party_id == anchor_party_id,
            CalculationRun.tax_type == run_type,
            CalculationRun.tax_period == analysis_period,
            CalculationRun.run_status == "SUCCEEDED",
        )
        .order_by(CalculationRun.id.desc())
        .limit(1)
    )


def _expected_component_keys(plan: dict[str, Any]) -> set[tuple[str, int]]:
    snapshot = plan["source_snapshot"]
    if plan["basis"] == "ACCRUAL":
        return {
            (str(row["component_type"]), int(row["fulfillment_fact_id"]))
            for row in snapshot["components"]
        }
    return {
        ("ENTITY_VAT_LEDGER", int(row["entity_vat_ledger_id"]))
        for row in snapshot["ledgers"]
    }


def _actual_component_keys(session: Session, *, result_id: int) -> set[tuple[str, int]]:
    rows = session.scalars(
        select(GroupPenetrationComponent)
        .where(GroupPenetrationComponent.result_id == result_id)
        .order_by(GroupPenetrationComponent.id)
    ).all()
    keys: set[tuple[str, int]] = set()
    for row in rows:
        if row.fulfillment_fact_id is not None:
            keys.add((str(row.component_type), int(row.fulfillment_fact_id)))
        elif row.entity_vat_ledger_id is not None:
            keys.add((str(row.component_type), int(row.entity_vat_ledger_id)))
        else:
            keys.add((str(row.component_type), -1))
    return keys


def build_one(
    session: Session,
    *,
    anchor_entity: str,
    period: str | date,
    basis: str,
    created_by: str,
    expected_input_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    if _head(session) != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    basis = basis.strip().upper()
    anchor = _anchor(session, anchor_entity)
    analysis_period = month_start(period)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
        {"scope": f"GROUP_PENETRATION:{basis}:{anchor.party_id}:{analysis_period.isoformat()}"},
    )
    plan = make_plan(session, anchor_entity=anchor_entity, period=analysis_period, basis=basis)
    if plan["status"] != "READY":
        require_cash_penetration()
    input_hash = plan["input_snapshot_sha256"]
    if expected_input_snapshot_sha256 is not None and expected_input_snapshot_sha256 != input_hash:
        raise ValueError("stale Task18 plan: source snapshot changed after PLAN review")
    result_hash = plan["result_sha256"]
    run_type = plan["run_type"]
    prior = _latest_successful_run(session, anchor_party_id=anchor.party_id, analysis_period=analysis_period, run_type=run_type)
    if prior is not None and prior.ruleset_version == RULESET_VERSION and prior.input_snapshot_sha256 == input_hash and prior.result_sha256 == result_hash:
        existing = session.scalar(select(GroupPenetrationResult).where(GroupPenetrationResult.calculation_run_id == prior.id))
        if (
            existing is not None
            and existing.result_sha256 == result_hash
            and _actual_component_keys(session, result_id=existing.id) == _expected_component_keys(plan)
        ):
            return {
                "status": "NO_CHANGE",
                "calculation_run_id": prior.id,
                "result_id": existing.id,
                "basis": basis,
                "analysis_period": str(analysis_period),
                "input_snapshot_sha256": input_hash,
            }

    run_kind = "RESTATEMENT" if prior is not None else "STANDARD"
    run = CalculationRun(
        reporting_party_id=anchor.party_id,
        tax_type=run_type,
        tax_period=analysis_period,
        run_kind=run_kind,
        run_status="SUCCEEDED",
        ruleset_version=RULESET_VERSION,
        input_snapshot_sha256=input_hash,
        result_sha256=result_hash,
        supersedes_run_id=prior.id if prior is not None else None,
        created_by=created_by,
        note=f"Task18 Group Penetration {basis}",
        completed_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()

    values = plan["result"]
    kwargs: dict[str, Any] = {
        "calculation_run_id": run.id,
        "anchor_party_id": anchor.party_id,
        "analysis_period": analysis_period,
        "basis": basis,
        "status": "READY",
        "cycle_detected": False,
        "max_depth": int(values["max_depth"]),
        "input_snapshot_sha256": input_hash,
        "result_sha256": result_hash,
    }
    if basis == "ACCRUAL":
        kwargs.update(
            external_revenue=Decimal(values["external_revenue"]),
            external_leaf_cost=Decimal(values["external_leaf_cost"]),
            internal_eliminated=Decimal(values["internal_eliminated"]),
            group_gross_margin=Decimal(values["group_gross_margin"]),
        )
    elif basis == "TAX":
        kwargs.update(
            tax_output_vat=Decimal(values["tax_output_vat"]),
            tax_input_vat=Decimal(values["tax_input_vat"]),
            tax_prepayment=Decimal(values["tax_prepayment"]),
            tax_payable_after_prepayment=Decimal(values["tax_payable_after_prepayment"]),
        )
    result = GroupPenetrationResult(**kwargs)
    session.add(result)
    session.flush()

    snapshot = plan["source_snapshot"]
    if basis == "ACCRUAL":
        for row in snapshot["components"]:
            session.add(GroupPenetrationComponent(
                result_id=result.id,
                component_type=row["component_type"],
                amount=Decimal(row["amount"]),
                fulfillment_fact_id=row["fulfillment_fact_id"],
                depth=int(row["depth"]),
                path=row["path"],
            ))
    else:
        for row in snapshot["ledgers"]:
            session.add(GroupPenetrationComponent(
                result_id=result.id,
                component_type="ENTITY_VAT_LEDGER",
                amount=Decimal(row["vat_payable_after_prepayment"]),
                entity_vat_ledger_id=row["entity_vat_ledger_id"],
                depth=0,
                path=f"reporting_party:{row['reporting_party_id']}",
            ))
    session.flush()
    return {
        "status": "BUILT",
        "calculation_run_id": run.id,
        "result_id": result.id,
        "basis": basis,
        "analysis_period": str(analysis_period),
        "input_snapshot_sha256": input_hash,
        "result_sha256": result_hash,
    }


def _write_json(path: str | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_cmd = sub.add_parser("plan")
    plan_cmd.add_argument("--anchor-entity", required=True)
    plan_cmd.add_argument("--period", required=True)
    plan_cmd.add_argument("--basis", required=True, choices=["ACCRUAL", "TAX", "CASH"])
    plan_cmd.add_argument("--json")
    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--anchor-entity", required=True)
    apply_cmd.add_argument("--period", required=True)
    apply_cmd.add_argument("--basis", required=True, choices=["ACCRUAL", "TAX", "CASH"])
    apply_cmd.add_argument("--created-by", required=True)
    apply_cmd.add_argument("--expected-input-sha256")
    apply_cmd.add_argument("--confirm-database", required=True)
    apply_cmd.add_argument("--json")
    args = parser.parse_args()
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    with Session(engine) as session:
        if args.command == "plan":
            payload = make_plan(session, anchor_entity=args.anchor_entity, period=args.period, basis=args.basis)
        else:
            database = session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
            if database != args.confirm_database:
                raise SystemExit(f"--confirm-database mismatch: expected {database!r}")
            payload = build_one(
                session,
                anchor_entity=args.anchor_entity,
                period=args.period,
                basis=args.basis,
                created_by=args.created_by,
                expected_input_snapshot_sha256=args.expected_input_sha256,
            )
            session.commit()
        _write_json(args.json, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
