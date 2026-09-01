#!/usr/bin/env python3
"""Task15 deterministic Entity Tax Ledger builder.

PLAN is the default and is read-only.  APPLY re-reads the reviewed evidence
inside a PostgreSQL transaction, verifies the saved plan, then writes one
successful ``ENTITY_TAX`` run, its period state, and exactly three typed
components.  Revenue and real cost are explicit reviewed management inputs;
VAT is only an official current ``EntityVatLedger`` dependency.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.domain.tax.entity_tax_ledger import (
    ENTITY_TAX_TYPE,
    RULESET_VERSION,
    EntityTaxEvidenceError,
    EntityTaxManagementInputView,
    EntityTaxRuleView,
    calculate_entity_tax_ledger,
    canonical_sha256,
    month_start,
    resolve_effective_tax_rule,
    select_latest_reviewed_management_inputs,
)
from app.models import (
    EntityTaxLedger,
    EntityTaxLedgerComponent,
    EntityTaxManagementInput,
    TaxRule,
)
from app.v3_party_models import InternalEntity
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_vat_ledger_models import EntityVatLedger


EXPECTED_HEAD = "87_v3_entity_tax_ledgers"
PLAN_KIND = "V3_TASK15_ENTITY_TAX_LEDGER_PLAN"
RESULT_KIND = "V3_TASK15_ENTITY_TAX_LEDGER_RESULT"
DISCOVERY_KIND = "V3_TASK15_ENTITY_TAX_LEDGER_DISCOVERY"
DEFAULT_CIT_RULE_CODE = "CIT_GENERAL"


@dataclass(frozen=True)
class _Evidence:
    entity: InternalEntity
    tax_period: date
    management_inputs: tuple[EntityTaxManagementInput, ...]
    tax_rules: tuple[TaxRule, ...]
    vat_ledger: EntityVatLedger
    vat_run: CalculationRun
    vat_state: TaxPeriodState
    calculation: Any
    source_snapshot: dict[str, Any]


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task15 Entity Tax Ledger builder is PostgreSQL-only")
    return value


def _canonical_hash(payload: Any) -> str:
    """Keep the CLI/plan hash representation identical to the domain hash."""

    return canonical_sha256(payload)


def _period(value: str | date) -> date:
    try:
        return month_start(value)
    except (TypeError, ValueError, EntityTaxEvidenceError) as exc:
        raise ValueError("period must be a valid YYYY-MM or ISO date") from exc


def _head(session: Session) -> str:
    rows = session.execute(
        text("SELECT version_num FROM alembic_version_tax")
    ).scalars().all()
    heads = {str(value) for value in rows if value}
    if heads != {EXPECTED_HEAD}:
        raise ValueError(
            f"formal DB head must be {EXPECTED_HEAD}; got {sorted(heads)}"
        )
    return EXPECTED_HEAD


def _current_database(session: Session) -> str:
    return str(
        session.execute(text("SELECT current_database()")).scalar_one()
    )


def _entity(session: Session, entity_code: str) -> InternalEntity:
    code = str(entity_code or "").strip()
    if not code:
        raise ValueError("entity code is required")
    row = session.scalar(
        select(InternalEntity).where(InternalEntity.canonical_code == code)
    )
    if row is None:
        raise ValueError(f"unknown internal entity: {code}")
    if row.legal_entity is not True:
        raise ValueError(f"entity {code} is not a legal entity")
    if row.active is not True:
        raise ValueError(f"entity {code} is inactive")
    return row


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _input_view(row: EntityTaxManagementInput) -> EntityTaxManagementInputView:
    return EntityTaxManagementInputView(
        id=int(row.id) if row.id is not None else None,
        reporting_party_id=int(row.reporting_party_id),
        tax_period=row.tax_period,
        input_type=str(row.input_type),
        input_version=int(row.input_version),
        amount=Decimal(row.amount),
        reviewed=bool(row.reviewed),
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        source=str(row.source or ""),
        source_document_id=(
            int(row.source_document_id)
            if row.source_document_id is not None
            else None
        ),
        note=row.note,
    )


def _rule_view(row: TaxRule) -> EntityTaxRuleView:
    effective_to = str(row.effective_to or "").strip() or None
    return EntityTaxRuleView(
        id=int(row.id) if row.id is not None else None,
        code=str(row.code or "").strip(),
        rate=Decimal(row.rate),
        effective_from=str(row.effective_from or "").strip(),
        effective_to=effective_to,
        reviewed=bool(row.reviewed),
        source=str(row.source or "").strip() or None,
        note=str(row.note or "").strip() or None,
    )


def _input_snapshot_row(row: EntityTaxManagementInput) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "reporting_party_id": int(row.reporting_party_id),
        "tax_period": _iso(row.tax_period),
        "input_type": str(row.input_type),
        "input_version": int(row.input_version),
        "amount": str(Decimal(row.amount)),
        "reviewed": bool(row.reviewed),
        "reviewed_by": row.reviewed_by,
        "reviewed_at": _iso(row.reviewed_at),
        "source": str(row.source or ""),
        "source_document_id": row.source_document_id,
        "note": row.note,
    }


def _rule_snapshot_row(row: TaxRule) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "code": str(row.code),
        "rate": str(Decimal(row.rate)),
        "effective_from": str(row.effective_from),
        "effective_to": str(row.effective_to or "") or None,
        "reviewed": bool(row.reviewed),
        "source": str(row.source or ""),
        "note": str(row.note or ""),
    }


def _vat_snapshot(ledger: EntityVatLedger, run: CalculationRun) -> dict[str, Any]:
    return {
        "id": int(ledger.id),
        "calculation_run_id": int(ledger.calculation_run_id),
        "reporting_party_id": int(ledger.reporting_party_id),
        "tax_period": _iso(ledger.tax_period),
        "calculation_run": {
            "id": int(run.id),
            "reporting_party_id": int(run.reporting_party_id),
            "tax_type": str(run.tax_type),
            "tax_period": _iso(run.tax_period),
            "run_kind": str(run.run_kind),
            "run_status": str(run.run_status),
            "ruleset_version": str(run.ruleset_version),
            "input_snapshot_sha256": str(run.input_snapshot_sha256),
            "result_sha256": str(run.result_sha256),
        },
        "result": {
            "opening_input_credit": str(Decimal(ledger.opening_input_credit)),
            "output_vat": str(Decimal(ledger.output_vat)),
            "input_vat": str(Decimal(ledger.input_vat)),
            "tax_prepayment": str(Decimal(ledger.tax_prepayment)),
            "vat_payable_before_prepayment": str(
                Decimal(ledger.vat_payable_before_prepayment)
            ),
            "closing_input_credit": str(Decimal(ledger.closing_input_credit)),
            "vat_payable_after_prepayment": str(
                Decimal(ledger.vat_payable_after_prepayment)
            ),
            "unapplied_tax_prepayment": str(
                Decimal(ledger.unapplied_tax_prepayment)
            ),
        },
    }


def _state_snapshot(state: TaxPeriodState) -> dict[str, Any]:
    return {
        "state": str(state.state),
        "current_run_id": (
            int(state.current_run_id) if state.current_run_id is not None else None
        ),
        "closed_run_id": (
            int(state.closed_run_id) if state.closed_run_id is not None else None
        ),
        "state_version": int(state.state_version),
    }


def _state_summary(
    session: Session,
    reporting_party_id: int,
    tax_period: date,
    *,
    tax_type: str = ENTITY_TAX_TYPE,
) -> dict[str, Any]:
    state = session.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == reporting_party_id,
            TaxPeriodState.tax_type == tax_type,
            TaxPeriodState.tax_period == tax_period,
        )
    )
    if state is None:
        return {
            "state": None,
            "current_run_id": None,
            "closed_run_id": None,
            "state_version": None,
            "restatement_required": False,
        }
    return {
        **_state_snapshot(state),
        "restatement_required": state.state == "CLOSED",
    }


def _current_vat_evidence(
    session: Session,
    reporting_party_id: int,
    tax_period: date,
) -> tuple[EntityVatLedger, CalculationRun, TaxPeriodState]:
    state = session.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == reporting_party_id,
            TaxPeriodState.tax_type == "VAT",
            TaxPeriodState.tax_period == tax_period,
        )
    )
    if state is None or state.current_run_id is None:
        raise EntityTaxEvidenceError(
            "official current EntityVatLedger result is required for the scope"
        )
    run = session.get(CalculationRun, state.current_run_id)
    if run is None:
        raise EntityTaxEvidenceError(
            "VAT TaxPeriodState current CalculationRun is unavailable"
        )
    if (
        run.reporting_party_id != reporting_party_id
        or run.tax_type != "VAT"
        or run.tax_period != tax_period
        or run.run_status != "SUCCEEDED"
        or run.result_sha256 is None
        or len(str(run.result_sha256)) != 64
    ):
        raise EntityTaxEvidenceError(
            "official current EntityVatLedger requires a SUCCEEDED VAT run"
        )
    ledgers = session.scalars(
        select(EntityVatLedger).where(
            EntityVatLedger.calculation_run_id == run.id,
        )
    ).all()
    if len(ledgers) != 1:
        raise EntityTaxEvidenceError(
            "official current VAT CalculationRun must have exactly one EntityVatLedger"
        )
    ledger = ledgers[0]
    if (
        ledger.reporting_party_id != reporting_party_id
        or ledger.tax_period != tax_period
    ):
        raise EntityTaxEvidenceError(
            "official EntityVatLedger does not match the entity-tax scope"
        )
    return ledger, run, state


def _read_evidence(
    session: Session,
    *,
    entity: InternalEntity,
    tax_period: date,
    cit_rule_code: str,
    input_versions: Mapping[str, int] | None = None,
) -> _Evidence:
    inputs = tuple(
        session.scalars(
            select(EntityTaxManagementInput)
            .where(
                EntityTaxManagementInput.reporting_party_id == entity.party_id,
                EntityTaxManagementInput.tax_period == tax_period,
            )
            .order_by(
                EntityTaxManagementInput.input_type,
                EntityTaxManagementInput.input_version,
                EntityTaxManagementInput.id,
            )
        ).all()
    )
    rules = tuple(
        session.scalars(
            select(TaxRule)
            .where(TaxRule.code == str(cit_rule_code or "").strip())
            .order_by(TaxRule.id)
        ).all()
    )
    vat_ledger, vat_run, vat_state = _current_vat_evidence(
        session, int(entity.party_id), tax_period
    )

    # Run the pure selector and calculator against ORM evidence.  This keeps
    # all validation (review, version, amount, period, rule effectiveness,
    # rounding and hashes) in the deterministic domain module.
    selected_views = select_latest_reviewed_management_inputs(
        tuple(_input_view(row) for row in inputs),
        reporting_party_id=int(entity.party_id),
        tax_period=tax_period,
        input_versions=input_versions,
    )
    rule = resolve_effective_tax_rule(
        tuple(_rule_view(row) for row in rules),
        tax_period=tax_period,
        rule_code=cit_rule_code,
    )
    calculation = calculate_entity_tax_ledger(
        reporting_party_id=int(entity.party_id),
        tax_period=tax_period,
        management_inputs=tuple(_input_view(row) for row in inputs),
        tax_rules=tuple(_rule_view(row) for row in rules),
        vat_ledger=vat_ledger,
        vat_calculation_run=vat_run,
        entity=entity,
        cit_rule_code=cit_rule_code,
        input_versions=input_versions,
    )

    source_snapshot = {
        "snapshot_version": "V3_ENTITY_TAX_LEDGER_SOURCE_SNAPSHOT_V1",
        "scope": {
            "reporting_party_id": int(entity.party_id),
            "tax_period": tax_period.isoformat(),
            "entity_code": str(entity.canonical_code),
            "legal_entity": bool(entity.legal_entity),
        },
        "management_inputs": [_input_snapshot_row(row) for row in inputs],
        "selected_management_inputs": [row.as_dict() for row in selected_views],
        "tax_rules": [_rule_snapshot_row(row) for row in rules],
        "selected_cit_rule": rule.as_dict(),
        "official_vat": _vat_snapshot(vat_ledger, vat_run),
        "vat_period_state": _state_snapshot(vat_state),
    }
    return _Evidence(
        entity=entity,
        tax_period=tax_period,
        management_inputs=inputs,
        tax_rules=rules,
        vat_ledger=vat_ledger,
        vat_run=vat_run,
        vat_state=vat_state,
        calculation=calculation,
        source_snapshot=source_snapshot,
    )


def make_plan(
    session: Session,
    *,
    entity_code: str,
    period: str | date,
    cit_rule_code: str = DEFAULT_CIT_RULE_CODE,
    input_versions: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    head = _head(session)
    entity = _entity(session, entity_code)
    tax_period = _period(period)
    evidence = _read_evidence(
        session,
        entity=entity,
        tax_period=tax_period,
        cit_rule_code=cit_rule_code,
        input_versions=input_versions,
    )
    calculation = evidence.calculation
    core = {
        "kind": PLAN_KIND,
        "version": 1,
        "database": _current_database(session),
        "alembic_head": head,
        "entity_code": str(entity.canonical_code),
        "reporting_party_id": int(entity.party_id),
        "tax_period": tax_period.isoformat(),
        "ruleset_version": RULESET_VERSION,
        "cit_rule_code": str(cit_rule_code).strip(),
        "input_snapshot_sha256": calculation.input_snapshot_sha256,
        "period_state": _state_summary(
            session, int(entity.party_id), tax_period, tax_type=ENTITY_TAX_TYPE
        ),
        "source_snapshot": evidence.source_snapshot,
        "calculation": calculation.as_dict(),
    }
    return {**core, "plan_digest": _canonical_hash(core)}


def _load_plan(path: str | Path) -> dict[str, Any]:
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Task15 plan: {path}") from exc
    if not isinstance(plan, dict):
        raise ValueError("Task15 plan must be a JSON object")
    if plan.get("kind") != PLAN_KIND or plan.get("version") != 1:
        raise ValueError("unsupported Task15 Entity Tax Ledger plan")
    digest = plan.get("plan_digest")
    core = {key: value for key, value in plan.items() if key != "plan_digest"}
    if digest != _canonical_hash(core):
        raise ValueError("Task15 plan_digest mismatch")
    required = {
        "database",
        "alembic_head",
        "entity_code",
        "reporting_party_id",
        "tax_period",
        "ruleset_version",
        "cit_rule_code",
        "input_snapshot_sha256",
        "period_state",
        "source_snapshot",
        "calculation",
    }
    missing = sorted(required.difference(plan))
    if missing:
        raise ValueError(f"Task15 plan is missing fields: {', '.join(missing)}")
    return plan


def _lock_scope(session: Session, reporting_party_id: int, tax_period: date) -> None:
    # Unit callers may use a non-PostgreSQL bind; the production builder and
    # all APPLY paths are PostgreSQL-only, where this lock serializes the
    # source reread and current-run replacement for one entity/month.
    if session.connection().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {
                "lock_key": (
                    f"entity-tax-ledger:{int(reporting_party_id)}:"
                    f"{tax_period.isoformat()}"
                )
            },
        )


def _same_decimal(left: Any, right: Any) -> bool:
    return Decimal(left) == Decimal(right)


def _assert_current_result_matches(
    run: CalculationRun,
    ledger: EntityTaxLedger,
    calculation: Any,
) -> None:
    expected = calculation
    checks = {
        "reporting_party_id": (run.reporting_party_id, expected.reporting_party_id),
        "tax_period": (run.tax_period, expected.tax_period),
        "tax_type": (run.tax_type, ENTITY_TAX_TYPE),
        "run_status": (run.run_status, "SUCCEEDED"),
        "ruleset_version": (run.ruleset_version, expected.rule_version),
        "input_snapshot_sha256": (
            run.input_snapshot_sha256,
            expected.input_snapshot_sha256,
        ),
        "result_sha256": (run.result_sha256, expected.result_sha256),
        "ledger_reporting_party_id": (
            ledger.reporting_party_id,
            expected.reporting_party_id,
        ),
        "ledger_tax_period": (ledger.tax_period, expected.tax_period),
        "entity_vat_ledger_id": (
            ledger.entity_vat_ledger_id,
            expected.entity_vat_ledger_id,
        ),
        "ledger_rule_version": (ledger.rule_version, expected.rule_version),
        "ledger_input_snapshot_sha256": (
            ledger.input_snapshot_sha256,
            expected.input_snapshot_sha256,
        ),
        "ledger_result_sha256": (ledger.result_sha256, expected.result_sha256),
    }
    for field, (actual, wanted) in checks.items():
        if actual != wanted:
            raise ValueError(f"current ENTITY_TAX result mismatch: {field}")
    for field in ("revenue", "real_cost", "estimated_profit", "estimated_cit"):
        if not _same_decimal(getattr(ledger, field), getattr(expected, field)):
            raise ValueError(f"current ENTITY_TAX result mismatch: {field}")


def _current_entity_tax_run(
    session: Session,
    state: TaxPeriodState,
    calculation: Any,
) -> tuple[CalculationRun, EntityTaxLedger] | None:
    if state.current_run_id is None:
        raise ValueError("ENTITY_TAX period state has no current run")
    run = session.get(CalculationRun, state.current_run_id)
    if run is None:
        raise ValueError("ENTITY_TAX period state current run is unavailable")
    ledger = session.scalar(
        select(EntityTaxLedger).where(
            EntityTaxLedger.calculation_run_id == state.current_run_id
        )
    )
    if ledger is None:
        raise ValueError("current ENTITY_TAX run has no immutable ledger result")
    if run.input_snapshot_sha256 == calculation.input_snapshot_sha256:
        _assert_current_result_matches(run, ledger, calculation)
        return run, ledger
    if run.run_status != "SUCCEEDED" or run.tax_type != ENTITY_TAX_TYPE:
        raise ValueError("current ENTITY_TAX period state points to an invalid run")
    return run, ledger


def _state_equals_expected(
    actual: dict[str, Any], expected: Mapping[str, Any] | None
) -> bool:
    if expected is None:
        return True
    fields = ("state", "current_run_id", "closed_run_id", "state_version")
    if not all(actual.get(field) == expected.get(field) for field in fields):
        return False
    if "restatement_required" in expected:
        return actual.get("restatement_required") == expected.get("restatement_required")
    return True


def build_one(
    session: Session,
    *,
    entity_code: str,
    period: str | date,
    created_by: str,
    allow_restatement: bool = False,
    expected_input_snapshot_sha256: str | None = None,
    expected_source_snapshot: Mapping[str, Any] | None = None,
    expected_period_state: Mapping[str, Any] | None = None,
    cit_rule_code: str = DEFAULT_CIT_RULE_CODE,
    input_versions: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    if not str(created_by or "").strip():
        raise ValueError("created_by is required")
    _head(session)
    tax_period = _period(period)
    entity = _entity(session, entity_code)

    # The lock is acquired before the authoritative source reread.  No
    # calculation or write is allowed to use the pre-lock evidence.
    _lock_scope(session, int(entity.party_id), tax_period)
    _head(session)
    entity = _entity(session, entity_code)
    evidence = _read_evidence(
        session,
        entity=entity,
        tax_period=tax_period,
        cit_rule_code=cit_rule_code,
        input_versions=input_versions,
    )
    calculation = evidence.calculation
    input_hash = calculation.input_snapshot_sha256
    if expected_input_snapshot_sha256 is not None and input_hash != expected_input_snapshot_sha256:
        raise ValueError("stale Task15 plan: source input snapshot changed after PLAN review")
    if expected_source_snapshot is not None and evidence.source_snapshot != dict(expected_source_snapshot):
        raise ValueError("stale Task15 plan: source evidence changed after PLAN review")

    state = session.scalar(
        select(TaxPeriodState)
        .where(
            TaxPeriodState.reporting_party_id == entity.party_id,
            TaxPeriodState.tax_type == ENTITY_TAX_TYPE,
            TaxPeriodState.tax_period == tax_period,
        )
        .with_for_update()
    )
    state_before = _state_summary(
        session, int(entity.party_id), tax_period, tax_type=ENTITY_TAX_TYPE
    )
    if not _state_equals_expected(state_before, expected_period_state):
        raise ValueError("stale Task15 plan: ENTITY_TAX period state changed after PLAN review")
    if state is not None and state.state == "CLOSED" and not allow_restatement:
        raise ValueError("target ENTITY_TAX period is CLOSED; explicit --restatement is required")

    current = None
    if state is not None:
        current = _current_entity_tax_run(session, state, calculation)
        if current is not None and current[0].input_snapshot_sha256 == input_hash:
            return {
                "kind": RESULT_KIND,
                "status": "NO_CHANGE",
                "ledger_id": int(current[1].id),
                "calculation_run_id": int(current[0].id),
                "run_kind": str(current[0].run_kind),
                "reporting_party_id": int(entity.party_id),
                "entity_code": str(entity.canonical_code),
                "tax_period": tax_period.isoformat(),
                "input_snapshot_sha256": input_hash,
                "result_sha256": str(current[0].result_sha256),
                "calculation": calculation.as_dict(),
            }

    if state is not None and state.state == "CLOSED":
        if state.current_run_id is None:
            raise ValueError("CLOSED ENTITY_TAX period has no current run")
        run_kind = "RESTATEMENT"
        supersedes_run_id = int(state.current_run_id)
    else:
        run_kind = "STANDARD"
        supersedes_run_id = None

    # CalculationRun is born as DRAFT so its immutable transition trigger is
    # exercised.  It becomes SUCCEEDED before the period state and ledger are
    # inserted, which is required by the revision-87 scope guards.
    run = CalculationRun(
        reporting_party_id=int(entity.party_id),
        tax_type=ENTITY_TAX_TYPE,
        tax_period=tax_period,
        run_kind=run_kind,
        run_status="DRAFT",
        ruleset_version=RULESET_VERSION,
        input_snapshot_sha256=input_hash,
        supersedes_run_id=supersedes_run_id,
        created_by=str(created_by).strip(),
        note="Task15 Entity Tax Ledger build",
    )
    session.add(run)
    session.flush()
    run.result_sha256 = calculation.result_sha256
    run.run_status = "SUCCEEDED"
    run.completed_at = datetime.now(timezone.utc)
    session.flush()

    if state is None:
        state = TaxPeriodState(
            reporting_party_id=int(entity.party_id),
            tax_type=ENTITY_TAX_TYPE,
            tax_period=tax_period,
            state="OPEN",
            current_run_id=run.id,
        )
        session.add(state)
    else:
        state.current_run_id = run.id
    session.flush()

    if calculation.revenue_input_id is None or calculation.real_cost_input_id is None:
        raise ValueError("selected management inputs must have persistent IDs")
    ledger = EntityTaxLedger(
        calculation_run_id=int(run.id),
        reporting_party_id=int(entity.party_id),
        tax_period=tax_period,
        entity_vat_ledger_id=int(calculation.entity_vat_ledger_id),
        revenue=calculation.revenue,
        real_cost=calculation.real_cost,
        estimated_profit=calculation.estimated_profit,
        estimated_cit=calculation.estimated_cit,
        rule_version=calculation.rule_version,
        input_snapshot_sha256=calculation.input_snapshot_sha256,
        result_sha256=calculation.result_sha256,
    )
    session.add(ledger)
    session.flush()

    session.add_all(
        [
            EntityTaxLedgerComponent(
                ledger_id=int(ledger.id),
                component_type="REVENUE",
                amount=calculation.revenue,
                management_input_id=int(calculation.revenue_input_id),
            ),
            EntityTaxLedgerComponent(
                ledger_id=int(ledger.id),
                component_type="REAL_COST",
                amount=calculation.real_cost,
                management_input_id=int(calculation.real_cost_input_id),
            ),
            EntityTaxLedgerComponent(
                ledger_id=int(ledger.id),
                component_type="ESTIMATED_CIT",
                amount=calculation.estimated_cit,
                management_input_id=None,
            ),
        ]
    )
    session.flush()

    return {
        "kind": RESULT_KIND,
        "status": "BUILT",
        "ledger_id": int(ledger.id),
        "calculation_run_id": int(run.id),
        "run_kind": run_kind,
        "supersedes_run_id": supersedes_run_id,
        "reporting_party_id": int(entity.party_id),
        "entity_code": str(entity.canonical_code),
        "tax_period": tax_period.isoformat(),
        "input_snapshot_sha256": calculation.input_snapshot_sha256,
        "result_sha256": calculation.result_sha256,
        "result": calculation.as_dict(),
        "calculation": calculation.as_dict(),
        "source_snapshot": evidence.source_snapshot,
        "period_state": _state_summary(
            session, int(entity.party_id), tax_period, tax_type=ENTITY_TAX_TYPE
        ),
    }


def apply_plan(
    session: Session,
    plan: Mapping[str, Any],
    *,
    created_by: str,
    allow_restatement: bool = False,
) -> dict[str, Any]:
    """Apply one validated plan within the caller-owned transaction."""

    head = _head(session)
    if plan.get("alembic_head") != head or plan.get("ruleset_version") != RULESET_VERSION:
        raise ValueError("Task15 Alembic head or ruleset changed after PLAN review")
    current_db = _current_database(session)
    if plan.get("database") != current_db:
        raise ValueError("saved Task15 plan targets a different database")
    if str(plan.get("cit_rule_code") or "").strip() == "":
        raise ValueError("saved Task15 plan has no explicit CIT rule code")
    try:
        planned_party_id = int(plan["reporting_party_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("saved Task15 plan has an invalid reporting party") from exc
    planned_period = _period(str(plan["tax_period"]))
    planned_entity_code = str(plan["entity_code"] or "").strip()
    planned_source = plan.get("source_snapshot")
    if not isinstance(planned_source, Mapping):
        raise ValueError("saved Task15 plan has no source snapshot")
    planned_scope = planned_source.get("scope")
    if not isinstance(planned_scope, Mapping):
        raise ValueError("saved Task15 plan has no source scope")
    if (
        planned_scope.get("reporting_party_id") != planned_party_id
        or planned_scope.get("tax_period") != planned_period.isoformat()
        or planned_scope.get("entity_code") != planned_entity_code
    ):
        raise ValueError("saved Task15 plan scope is internally inconsistent")
    planned_calculation = plan.get("calculation")
    if not isinstance(planned_calculation, Mapping):
        raise ValueError("saved Task15 plan has no calculation payload")
    if planned_calculation.get("input_snapshot_sha256") != plan.get("input_snapshot_sha256"):
        raise ValueError("saved Task15 plan calculation hash is inconsistent")
    result = build_one(
        session,
        entity_code=str(plan["entity_code"]),
        period=str(plan["tax_period"]),
        created_by=created_by,
        allow_restatement=allow_restatement,
        expected_input_snapshot_sha256=str(plan["input_snapshot_sha256"]),
        expected_source_snapshot=plan.get("source_snapshot"),
        expected_period_state=plan.get("period_state"),
        cit_rule_code=str(plan["cit_rule_code"]),
    )
    if result.get("calculation") != dict(planned_calculation):
        raise ValueError("saved Task15 plan calculation changed after PLAN review")
    return {
        "database": current_db,
        "plan_digest": plan["plan_digest"],
        **result,
    }


def discover(session: Session) -> list[dict[str, Any]]:
    _head(session)
    entities = session.execute(
        select(InternalEntity.canonical_code, InternalEntity.party_id)
        .where(InternalEntity.active.is_(True), InternalEntity.legal_entity.is_(True))
        .order_by(InternalEntity.canonical_code)
    ).all()
    periods_by_party: dict[int, set[date]] = {}
    for party_id, period in session.execute(
        select(
            EntityTaxManagementInput.reporting_party_id,
            EntityTaxManagementInput.tax_period,
        )
    ).all():
        periods_by_party.setdefault(int(party_id), set()).add(period)
    for party_id, period in session.execute(
        select(EntityVatLedger.reporting_party_id, EntityVatLedger.tax_period)
    ).all():
        periods_by_party.setdefault(int(party_id), set()).add(period)

    items: list[dict[str, Any]] = []
    for code, party_id in entities:
        for tax_period in sorted(periods_by_party.get(int(party_id), set())):
            try:
                plan = make_plan(
                    session,
                    entity_code=str(code),
                    period=tax_period,
                )
                items.append(
                    {
                        "entity_code": str(code),
                        "reporting_party_id": int(party_id),
                        "period": tax_period.strftime("%Y-%m"),
                        "eligible": True,
                        "input_snapshot_sha256": plan["input_snapshot_sha256"],
                        "result_sha256": plan["calculation"]["result_sha256"],
                        "period_state": plan["period_state"],
                        "blocker": None,
                    }
                )
            except (ValueError, EntityTaxEvidenceError) as exc:
                items.append(
                    {
                        "entity_code": str(code),
                        "reporting_party_id": int(party_id),
                        "period": tax_period.strftime("%Y-%m"),
                        "eligible": False,
                        "blocker": str(exc),
                    }
                )
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--entity")
    parser.add_argument("--period")
    parser.add_argument("--cit-rule-code", default=DEFAULT_CIT_RULE_CODE)
    parser.add_argument("--plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restatement", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--created-by", default="v3-task15")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    if args.discover and (args.apply or args.plan or args.entity or args.period):
        raise SystemExit("--discover cannot be combined with PLAN/APPLY arguments")
    if args.restatement and not args.apply:
        raise SystemExit("--restatement is only valid with --apply")
    if args.cit_rule_code is None or not str(args.cit_rule_code).strip():
        raise SystemExit("--cit-rule-code must be nonblank")

    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            try:
                if args.discover:
                    result = {
                        "kind": DISCOVERY_KIND,
                        "database": _current_database(session),
                        "alembic_head": EXPECTED_HEAD,
                        "items": discover(session),
                    }
                elif not args.apply:
                    if args.plan:
                        raise SystemExit("--plan is used only with --apply")
                    if not args.entity or not args.period:
                        raise SystemExit("--entity and --period are required to generate a PLAN")
                    result = make_plan(
                        session,
                        entity_code=args.entity,
                        period=args.period,
                        cit_rule_code=args.cit_rule_code,
                    )
                else:
                    if not args.plan:
                        raise SystemExit("--apply requires an exact saved --plan file")
                    if args.entity or args.period or args.discover:
                        raise SystemExit("do not combine --entity/--period with --plan --apply")
                    if not str(args.created_by or "").strip():
                        raise SystemExit("--created-by must be nonblank")
                    current_db = _current_database(session)
                    if args.confirm_database != current_db:
                        raise SystemExit(
                            "--confirm-database must exactly match current_database()"
                        )
                    plan = _load_plan(args.plan)
                    if plan.get("database") != current_db:
                        raise SystemExit("saved Task15 plan targets a different database")
                    result = apply_plan(
                        session,
                        plan,
                        created_by=args.created_by,
                        allow_restatement=args.restatement,
                    )
                    session.commit()
            except BaseException:
                # This includes database-trigger failures: a failed APPLY must
                # not leave a run/state/ledger/component fragment behind.
                session.rollback()
                raise
    finally:
        engine.dispose()

    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
