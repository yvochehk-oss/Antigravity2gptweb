#!/usr/bin/env python3
"""Read-only Gate S15 for the V3 Entity Tax Ledger.

The gate is deliberately independent from the PLAN/APPLY writer.  It reads a
single repeatable-read, read-only PostgreSQL snapshot and checks the physical
revision-87 contract, the current VAT dependency, the deterministic domain
result, the typed components, and the CalculationRun/TaxPeriodState chain.

Exit codes are intentionally distinct:

* ``0`` -- PASS;
* ``1`` -- a revision-87 database is available but evidence or an invariant
  fails (including the required no-pilot fail-closed condition);
* ``2`` -- the gate is not runnable yet (for example a formal database still
  has revision 86).  The latter is returned as JSON instead of raising while
  probing revision-87 tables that do not exist yet.

No INSERT, UPDATE, DELETE, DDL, migration, seed, or transaction commit is
performed here.  ``--json`` only writes the verifier's own result artifact.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.domain.tax.entity_tax_ledger import (  # noqa: E402
    ENTITY_TAX_TYPE,
    RULESET_VERSION,
    EntityTaxManagementInputView,
    EntityTaxRuleView,
    EntityTaxEvidenceError,
    EntityTaxLedgerError,
    OfficialVatLedgerView,
    calculate_entity_tax_ledger,
    select_latest_reviewed_management_inputs,
)


EXPECTED_HEAD = "87_v3_entity_tax_ledgers"
VAT_TAX_TYPE = "VAT"
TASK15_TABLES = {
    "entity_tax_management_inputs",
    "entity_tax_ledgers",
    "entity_tax_ledger_components",
}

# Revision 87 owns these named, non-unique access paths.  Primary-key and
# unique-constraint indexes are checked separately below because PostgreSQL
# chooses their physical names.
REQUIRED_INDEXES: dict[str, set[str]] = {
    "entity_tax_management_inputs": {
        "ix_entity_tax_management_inputs_scope",
        "ix_entity_tax_management_inputs_source_document",
    },
    "entity_tax_ledgers": {"ix_entity_tax_ledgers_scope"},
    "entity_tax_ledger_components": {
        "ix_entity_tax_ledger_components_ledger",
        "ix_entity_tax_ledger_components_input",
    },
}

REQUIRED_TRIGGERS = {
    "trg_v3_guard_entity_tax_ledger_scope",
    "trg_v3_guard_entity_tax_ledger_period_state",
    "trg_v3_guard_entity_tax_ledger_component_source",
    "trg_v3_guard_entity_tax_ledger_reconciliation",
    "trg_v3_guard_entity_tax_ledger_component_reconciliation",
    "trg_v3_guard_entity_tax_ledger_immutable",
    "trg_v3_guard_entity_tax_ledger_component_immutable",
    "trg_v3_guard_entity_tax_management_input_mutation",
}

REQUIRED_FKS: dict[str, set[tuple[tuple[str, ...], str, tuple[str, ...]]]] = {
    "entity_tax_management_inputs": {
        (("reporting_party_id",), "internal_entities", ("party_id",)),
        (("source_document_id",), "source_documents", ("id",)),
    },
    "entity_tax_ledgers": {
        (("calculation_run_id",), "calculation_runs", ("id",)),
        (("reporting_party_id",), "internal_entities", ("party_id",)),
        (("entity_vat_ledger_id",), "entity_vat_ledgers", ("id",)),
    },
    "entity_tax_ledger_components": {
        (("ledger_id",), "entity_tax_ledgers", ("id",)),
        (("management_input_id",), "entity_tax_management_inputs", ("id",)),
    },
}

REQUIRED_UNIQUES: dict[str, set[tuple[str, ...]]] = {
    "entity_tax_management_inputs": {
        ("reporting_party_id", "tax_period", "input_type", "input_version"),
    },
    "entity_tax_ledgers": {
        ("calculation_run_id",),
        ("reporting_party_id", "tax_period", "calculation_run_id"),
    },
    "entity_tax_ledger_components": {
        ("ledger_id", "component_type"),
        ("ledger_id", "management_input_id"),
    },
}

REQUIRED_CHECKS: dict[str, set[str]] = {
    "entity_tax_management_inputs": {
        "ck_entity_tax_management_inputs_period_month_start",
        "ck_entity_tax_management_inputs_type",
        "ck_entity_tax_management_inputs_version_positive",
        "ck_entity_tax_management_inputs_amount_nonnegative",
        "ck_entity_tax_management_inputs_source_nonblank",
        "ck_entity_tax_management_inputs_reviewed",
    },
    "entity_tax_ledgers": {
        "ck_entity_tax_ledgers_period_month_start",
        "ck_entity_tax_ledgers_amounts_nonnegative",
        "ck_entity_tax_ledgers_profit_formula",
        "ck_entity_tax_ledgers_cit_nonnegative",
        "ck_entity_tax_ledgers_loss_zero_cit",
        "ck_entity_tax_ledgers_rule_version_nonblank",
        "ck_entity_tax_ledgers_input_hash_length",
        "ck_entity_tax_ledgers_result_hash_length",
    },
    "entity_tax_ledger_components": {
        "ck_entity_tax_ledger_components_type",
        "ck_entity_tax_ledger_components_amount_nonnegative",
        "ck_entity_tax_ledger_components_typed_source",
    },
}

PROHIBITED_COLUMNS = {"project_id", "entity_code", "invoice_date"}


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise ValueError("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise ValueError("Gate S15 is PostgreSQL-only")
    return value


def _disk_heads() -> set[str]:
    config = Config(str(ROOT / "alembic.ini"))
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        config.set_main_option("script_location", str(ROOT / script_location))
    return set(ScriptDirectory.from_config(config).get_heads())


def _table_rows(conn: Connection, table: str) -> list[dict[str, Any]]:
    """Read a table through mappings, keeping the gate independent of ORM state."""

    return [dict(row) for row in conn.execute(text(f'SELECT * FROM "{table}"')).mappings()]


def _table_exists(inspector: Any, table: str) -> bool:
    return table in set(inspector.get_table_names(schema="public"))


def _db_heads(conn: Connection, inspector: Any) -> set[str]:
    if not _table_exists(inspector, "alembic_version_tax"):
        return set()
    return {
        str(row[0])
        for row in conn.execute(text("SELECT version_num FROM alembic_version_tax"))
        if row[0]
    }


def _db_fks(inspector: Any, table: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    result: set[tuple[tuple[str, ...], str, tuple[str, ...]]] = set()
    for row in inspector.get_foreign_keys(table, schema="public"):
        result.add(
            (
                tuple(row.get("constrained_columns") or []),
                str(row.get("referred_table") or ""),
                tuple(row.get("referred_columns") or []),
            )
        )
    return result


def _db_uniques(inspector: Any, table: str) -> set[tuple[str, ...]]:
    result = {
        tuple(row.get("column_names") or [])
        for row in inspector.get_unique_constraints(table, schema="public")
    }
    result.update(
        tuple(row.get("column_names") or [])
        for row in inspector.get_indexes(table, schema="public")
        if row.get("unique")
    )
    return {value for value in result if value}


def _schema_contract(
    conn: Connection,
    inspector: Any,
    failures: list[str],
) -> dict[str, Any]:
    tables = set(inspector.get_table_names(schema="public"))
    missing_tables = sorted(TASK15_TABLES - tables)
    if missing_tables:
        failures.append(f"missing Task15 tables: {missing_tables}")

    columns: dict[str, list[str]] = {}
    prohibited: dict[str, list[str]] = {}
    indexes: dict[str, list[str]] = {}
    missing_indexes: dict[str, list[str]] = {}
    fks: dict[str, list[list[Any]]] = {}
    missing_fks: dict[str, list[list[Any]]] = {}
    uniques: dict[str, list[list[str]]] = {}
    missing_uniques: dict[str, list[list[str]]] = {}
    checks: dict[str, list[str]] = {}
    missing_checks: dict[str, list[str]] = {}

    for table in sorted(TASK15_TABLES):
        if table not in tables:
            columns[table] = []
            prohibited[table] = []
            indexes[table] = []
            missing_indexes[table] = sorted(REQUIRED_INDEXES[table])
            fks[table] = []
            missing_fks[table] = [list(value) for value in sorted(REQUIRED_FKS[table], key=repr)]
            uniques[table] = []
            missing_uniques[table] = [list(value) for value in sorted(REQUIRED_UNIQUES[table])]
            checks[table] = []
            missing_checks[table] = sorted(REQUIRED_CHECKS[table])
            continue

        table_columns = {str(row["name"]) for row in inspector.get_columns(table, schema="public")}
        columns[table] = sorted(table_columns)
        prohibited_columns = sorted(table_columns & PROHIBITED_COLUMNS)
        prohibited[table] = prohibited_columns
        if prohibited_columns:
            failures.append(f"{table} contains prohibited columns: {prohibited_columns}")

        index_names = {
            str(row.get("name"))
            for row in inspector.get_indexes(table, schema="public")
            if row.get("name")
        }
        indexes[table] = sorted(index_names)
        missing = sorted(REQUIRED_INDEXES[table] - index_names)
        missing_indexes[table] = missing
        if missing:
            failures.append(f"{table} missing Task15 indexes: {missing}")

        actual_fks = _db_fks(inspector, table)
        fks[table] = [
            [list(local), remote, list(remote_columns)]
            for local, remote, remote_columns in sorted(actual_fks, key=repr)
        ]
        missing = REQUIRED_FKS[table] - actual_fks
        missing_fks[table] = [
            [list(local), remote, list(remote_columns)]
            for local, remote, remote_columns in sorted(missing, key=repr)
        ]
        if missing:
            failures.append(f"{table} missing Task15 foreign keys: {missing_fks[table]}")

        actual_uniques = _db_uniques(inspector, table)
        uniques[table] = [list(value) for value in sorted(actual_uniques)]
        missing = REQUIRED_UNIQUES[table] - actual_uniques
        missing_uniques[table] = [list(value) for value in sorted(missing)]
        if missing:
            failures.append(f"{table} missing Task15 unique contract: {missing_uniques[table]}")

        actual_checks = {
            str(row.get("name") or "")
            for row in inspector.get_check_constraints(table, schema="public")
            if row.get("name")
        }
        checks[table] = sorted(actual_checks)
        missing = sorted(REQUIRED_CHECKS[table] - actual_checks)
        missing_checks[table] = missing
        if missing:
            failures.append(f"{table} missing Task15 checks: {missing}")

    trigger_rows = [
        dict(row)
        for row in conn.execute(
            text(
                """
                SELECT tgname AS trigger_name,
                       tgrelid::regclass::text AS table_name,
                       tgenabled AS enabled
                FROM pg_trigger
                WHERE NOT tgisinternal
                  AND tgrelid::regclass::text IN
                      ('entity_tax_management_inputs', 'entity_tax_ledgers',
                       'entity_tax_ledger_components')
                ORDER BY tgname
                """
            )
        ).mappings()
    ]
    trigger_names = {str(row["trigger_name"]) for row in trigger_rows}
    missing_triggers = sorted(REQUIRED_TRIGGERS - trigger_names)
    disabled_triggers = sorted(
        str(row["trigger_name"])
        for row in trigger_rows
        if str(row["trigger_name"]) in REQUIRED_TRIGGERS
        and str(row["enabled"]) != "O"
    )
    if missing_triggers:
        failures.append(f"missing Task15 protection triggers: {missing_triggers}")
    if disabled_triggers:
        failures.append(f"disabled Task15 protection triggers: {disabled_triggers}")

    return {
        "tables_present": sorted(TASK15_TABLES & tables),
        "missing_tables": missing_tables,
        "columns": columns,
        "prohibited_columns": prohibited,
        "indexes": indexes,
        "missing_indexes": missing_indexes,
        "foreign_keys": fks,
        "missing_foreign_keys": missing_fks,
        "uniques": uniques,
        "missing_uniques": missing_uniques,
        "checks": checks,
        "missing_checks": missing_checks,
        "triggers": trigger_rows,
        "missing_triggers": missing_triggers,
        "disabled_triggers": disabled_triggers,
    }


def _as_int(value: Any) -> int:
    return int(value)


def _as_decimal(value: Any) -> Decimal:
    return Decimal(value)


def _scope(row: Mapping[str, Any]) -> tuple[int, str, Any]:
    return (int(row["reporting_party_id"]), str(row["tax_type"]), row["tax_period"])


def _input_view(row: Mapping[str, Any]) -> EntityTaxManagementInputView:
    return EntityTaxManagementInputView(
        id=_as_int(row["id"]),
        reporting_party_id=_as_int(row["reporting_party_id"]),
        tax_period=row["tax_period"],
        input_type=str(row["input_type"] or ""),
        input_version=_as_int(row["input_version"]),
        amount=_as_decimal(row["amount"]),
        reviewed=bool(row["reviewed"]),
        reviewed_by=row.get("reviewed_by"),
        reviewed_at=row.get("reviewed_at"),
        source=str(row.get("source") or ""),
        source_document_id=(
            _as_int(row["source_document_id"])
            if row.get("source_document_id") is not None
            else None
        ),
        note=row.get("note"),
    )


def _rule_view(row: Mapping[str, Any]) -> EntityTaxRuleView:
    effective_to = str(row.get("effective_to") or "").strip() or None
    return EntityTaxRuleView(
        id=_as_int(row["id"]),
        code=str(row.get("code") or "").strip(),
        rate=_as_decimal(row["rate"]),
        effective_from=str(row.get("effective_from") or "").strip(),
        effective_to=effective_to,
        reviewed=bool(row.get("reviewed")),
        source=str(row.get("source") or "").strip() or None,
        note=str(row.get("note") or "").strip() or None,
    )


def _vat_view(
    ledger: Mapping[str, Any],
    run: Mapping[str, Any],
) -> tuple[OfficialVatLedgerView, dict[str, Any]]:
    view = OfficialVatLedgerView(
        id=_as_int(ledger["id"]),
        calculation_run_id=_as_int(ledger["calculation_run_id"]),
        reporting_party_id=_as_int(ledger["reporting_party_id"]),
        tax_period=ledger["tax_period"],
        tax_type=str(run.get("tax_type") or ""),
        run_status=str(run.get("run_status") or ""),
        result_sha256=(
            str(run["result_sha256"])
            if run.get("result_sha256") is not None
            else None
        ),
        official=True,
    )
    run_view = {
        "id": _as_int(run["id"]),
        "reporting_party_id": _as_int(run["reporting_party_id"]),
        "tax_type": str(run.get("tax_type") or ""),
        "tax_period": run["tax_period"],
        "run_status": str(run.get("run_status") or ""),
        "result_sha256": run.get("result_sha256"),
    }
    return view, run_view


def _same_decimal(left: Any, right: Any) -> bool:
    try:
        return _as_decimal(left) == _as_decimal(right)
    except (ArithmeticError, TypeError, ValueError):
        return False


def _row_result_error(
    failures: list[str],
    ledger_id: int,
    message: str,
    errors: set[int],
) -> None:
    errors.add(ledger_id)
    failures.append(f"entity tax ledger {ledger_id}: {message}")


def _check_entity_tax_ledgers(
    *,
    conn: Connection,
    failures: list[str],
    internal_entities: Mapping[int, Mapping[str, Any]],
    runs: Mapping[int, Mapping[str, Any]],
    states: Mapping[tuple[int, str, Any], Mapping[str, Any]],
    tax_ledgers: Iterable[Mapping[str, Any]],
    components_by_ledger: Mapping[int, list[Mapping[str, Any]]],
    inputs_by_scope: Mapping[tuple[int, Any], list[Mapping[str, Any]]],
    tax_rules: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate every ledger and return current-pilot and chain evidence."""

    ledger_rows = list(tax_ledgers)
    ledger_by_run: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    ledger_by_scope: dict[tuple[int, Any], list[Mapping[str, Any]]] = defaultdict(list)
    for ledger in ledger_rows:
        ledger_by_run[int(ledger["calculation_run_id"])].append(ledger)
        ledger_by_scope[(int(ledger["reporting_party_id"]), ledger["tax_period"])].append(ledger)

    errors: set[int] = set()
    candidate_current_ids: set[int] = set()
    chain_run_ids_by_scope: dict[tuple[int, Any], set[int]] = defaultdict(set)
    chain_errors: list[dict[str, Any]] = []
    tax_rule_matches: dict[int, dict[str, Any]] = {}

    # First validate each current ENTITY_TAX state and materialize its complete
    # direct supersession chain.  Historical ledger rows are required to be
    # present for every run in this chain.
    entity_states = [
        row for row in states.values() if str(row.get("tax_type")) == ENTITY_TAX_TYPE
    ]
    state_scope_counts: dict[tuple[int, Any], int] = defaultdict(int)
    for state in entity_states:
        party = int(state["reporting_party_id"])
        period = state["tax_period"]
        scope = (party, period)
        state_scope_counts[scope] += 1
        current_id = state.get("current_run_id")
        state_id = int(state["id"])
        if current_id is None:
            # A state without a current result is not a pilot.  A ledger in the
            # same scope is dealt with as an orphan below.
            continue
        current_id = int(current_id)
        current = runs.get(current_id)
        current_ledgers = ledger_by_run.get(current_id, [])
        if current is None:
            chain_errors.append({"state_id": state_id, "reason": "current run missing"})
            failures.append(f"ENTITY_TAX state {state_id} points to a missing current CalculationRun")
            continue
        if len(current_ledgers) != 1:
            chain_errors.append(
                {"state_id": state_id, "reason": "current ledger count", "count": len(current_ledgers)}
            )
            failures.append(
                f"ENTITY_TAX state {state_id} must point to exactly one current EntityTaxLedger; "
                f"found {len(current_ledgers)}"
            )
            continue
        current_ledger_id = int(current_ledgers[0]["id"])
        candidate_current_ids.add(current_ledger_id)

        if str(state.get("state")) == "OPEN":
            if state.get("closed_run_id") is not None or state.get("closed_by") is not None or state.get("closed_at") is not None:
                failures.append(f"OPEN ENTITY_TAX state {state_id} has CLOSED metadata")
            if str(current.get("run_kind")) != "STANDARD":
                failures.append(f"OPEN ENTITY_TAX state {state_id} current run is not STANDARD")
            if current.get("supersedes_run_id") is not None:
                failures.append(f"OPEN ENTITY_TAX state {state_id} current STANDARD run supersedes another run")
            chain_run_ids_by_scope[scope].add(current_id)
            continue

        if str(state.get("state")) != "CLOSED":
            failures.append(f"ENTITY_TAX state {state_id} has invalid state {state.get('state')!r}")
            continue

        closed_id = state.get("closed_run_id")
        if closed_id is None:
            failures.append(f"CLOSED ENTITY_TAX state {state_id} has no closed_run_id")
            continue
        closed_id = int(closed_id)
        closed = runs.get(closed_id)
        if closed is None:
            failures.append(f"CLOSED ENTITY_TAX state {state_id} closed run is missing")
            continue
        if str(closed.get("run_kind")) != "STANDARD" or str(closed.get("run_status")) != "SUCCEEDED":
            failures.append(f"CLOSED ENTITY_TAX state {state_id} closed anchor is not a SUCCEEDED STANDARD run")
        if (
            int(closed.get("reporting_party_id")) != party
            or str(closed.get("tax_type")) != ENTITY_TAX_TYPE
            or closed.get("tax_period") != period
        ):
            failures.append(f"CLOSED ENTITY_TAX state {state_id} closed anchor scope mismatch")
        if len(ledger_by_run.get(closed_id, [])) != 1:
            failures.append(f"CLOSED ENTITY_TAX state {state_id} closed anchor ledger history is missing or duplicated")

        cursor = current_id
        seen: set[int] = set()
        chain_ok = True
        while True:
            if cursor in seen:
                chain_ok = False
                failures.append(f"CLOSED ENTITY_TAX state {state_id} supersession chain cycles at run {cursor}")
                break
            seen.add(cursor)
            chain_run_ids_by_scope[scope].add(cursor)
            row = runs.get(cursor)
            if row is None:
                chain_ok = False
                failures.append(f"CLOSED ENTITY_TAX state {state_id} supersession chain run {cursor} is missing")
                break
            if (
                int(row.get("reporting_party_id")) != party
                or str(row.get("tax_type")) != ENTITY_TAX_TYPE
                or row.get("tax_period") != period
                or str(row.get("run_status")) != "SUCCEEDED"
            ):
                chain_ok = False
                failures.append(f"CLOSED ENTITY_TAX state {state_id} has invalid run scope/status at run {cursor}")
            if len(ledger_by_run.get(cursor, [])) != 1:
                chain_ok = False
                failures.append(f"CLOSED ENTITY_TAX state {state_id} lacks one historical ledger for run {cursor}")
            if cursor == closed_id:
                if str(row.get("run_kind")) != "STANDARD" or row.get("supersedes_run_id") is not None:
                    chain_ok = False
                    failures.append(f"CLOSED ENTITY_TAX state {state_id} closed anchor is not an unsuperseded STANDARD run")
                break
            if str(row.get("run_kind")) != "RESTATEMENT" or row.get("supersedes_run_id") is None:
                chain_ok = False
                failures.append(
                    f"CLOSED ENTITY_TAX state {state_id} current/history run {cursor} must be a RESTATEMENT directly superseding the prior current run"
                )
                break
            parent_id = int(row["supersedes_run_id"])
            parent = runs.get(parent_id)
            if parent is None:
                chain_ok = False
                failures.append(f"CLOSED ENTITY_TAX state {state_id} run {cursor} supersedes a missing run")
                break
            # Moving one cursor at a time is the direct-supersession check.
            cursor = parent_id
        if not chain_ok:
            chain_errors.append({"state_id": state_id, "reason": "invalid supersession chain"})

    duplicate_state_scopes = sorted(
        [scope for scope, count in state_scope_counts.items() if count != 1], key=repr
    )
    if duplicate_state_scopes:
        failures.append(f"duplicate ENTITY_TAX current state scopes: {duplicate_state_scopes}")

    # Validate the physical legal-entity scope and deterministic result for
    # each row.  A candidate CIT rule is discovered from the result hash; the
    # row itself stores only the ruleset and hashes, so the gate must not guess
    # a tax rate or silently default to one.
    rule_views = [_rule_view(row) for row in tax_rules]
    for raw_ledger in ledger_rows:
        ledger_id = int(raw_ledger["id"])
        party = int(raw_ledger["reporting_party_id"])
        period = raw_ledger["tax_period"]
        scope = (party, period)
        entity = internal_entities.get(party)
        if entity is None:
            _row_result_error(failures, ledger_id, "reporting party is missing from internal_entities", errors)
            continue
        if entity.get("legal_entity") is not True:
            _row_result_error(failures, ledger_id, "reporting party is not legal_entity=true", errors)
        if entity.get("active") is not True:
            _row_result_error(failures, ledger_id, "reporting party is inactive", errors)

        run_id = int(raw_ledger["calculation_run_id"])
        run = runs.get(run_id)
        if run is None:
            _row_result_error(failures, ledger_id, "CalculationRun is missing", errors)
            continue
        if (
            int(run.get("reporting_party_id")) != party
            or str(run.get("tax_type")) != ENTITY_TAX_TYPE
            or run.get("tax_period") != period
            or str(run.get("run_status")) != "SUCCEEDED"
        ):
            _row_result_error(failures, ledger_id, "CalculationRun scope/status is invalid", errors)
        if str(run.get("ruleset_version")) != RULESET_VERSION:
            _row_result_error(failures, ledger_id, f"ruleset is not {RULESET_VERSION}", errors)
        if str(raw_ledger.get("rule_version")) != str(run.get("ruleset_version")):
            _row_result_error(failures, ledger_id, "ledger rule_version does not match CalculationRun", errors)
        for field in ("input_snapshot_sha256", "result_sha256"):
            if str(raw_ledger.get(field) or "") != str(run.get(field) or ""):
                _row_result_error(failures, ledger_id, f"ledger {field} does not match CalculationRun", errors)
            if len(str(run.get(field) or "")) != 64:
                _row_result_error(failures, ledger_id, f"CalculationRun {field} is not a SHA-256 hex-length value", errors)

        state = states.get((party, ENTITY_TAX_TYPE, period))
        if state is None or state.get("current_run_id") is None or int(state["current_run_id"]) != run_id:
            _row_result_error(failures, ledger_id, "ledger is not the current TaxPeriodState ENTITY_TAX result", errors)

        # The VAT reference is official only when both VAT state and run point
        # to exactly one successful ledger in the same party/month scope.
        vat_state = states.get((party, VAT_TAX_TYPE, period))
        vat_ledger = None
        vat_run = None
        vat_ref_id = raw_ledger.get("entity_vat_ledger_id")
        if vat_state is None or vat_state.get("current_run_id") is None:
            _row_result_error(failures, ledger_id, "official current VAT TaxPeriodState is missing", errors)
        else:
            vat_run_id = int(vat_state["current_run_id"])
            vat_run = runs.get(vat_run_id)
            if vat_run is None:
                _row_result_error(failures, ledger_id, "VAT TaxPeriodState current run is missing", errors)
            elif (
                int(vat_run.get("reporting_party_id")) != party
                or str(vat_run.get("tax_type")) != VAT_TAX_TYPE
                or vat_run.get("tax_period") != period
                or str(vat_run.get("run_status")) != "SUCCEEDED"
                or len(str(vat_run.get("result_sha256") or "")) != 64
            ):
                _row_result_error(failures, ledger_id, "VAT current run is not a SUCCEEDED same-scope result", errors)
            else:
                # The caller supplies all VAT ledger rows through a compact
                # query below; this dictionary is filled in run() and passed
                # through a private attribute on the connection context.
                vat_ledger_rows = _vat_ledgers_for_scope(conn, party, period)
                current_vat_ledgers = [
                    row for row in vat_ledger_rows if int(row["calculation_run_id"]) == vat_run_id
                ]
                if len(current_vat_ledgers) != 1:
                    _row_result_error(
                        failures,
                        ledger_id,
                        f"VAT current run must have exactly one EntityVatLedger; found {len(current_vat_ledgers)}",
                        errors,
                    )
                else:
                    vat_ledger = current_vat_ledgers[0]
                    if (
                        int(vat_ledger["reporting_party_id"]) != party
                        or vat_ledger["tax_period"] != period
                    ):
                        _row_result_error(failures, ledger_id, "official VAT ledger scope mismatch", errors)
                    if vat_ref_id is None or int(vat_ref_id) != int(vat_ledger["id"]):
                        _row_result_error(failures, ledger_id, "entity_vat_ledger_id is not the official current VAT ledger", errors)

        # Components are intentionally checked before calculating so a
        # malformed type/source cannot be hidden by a correct total.
        components = list(components_by_ledger.get(ledger_id, []))
        component_types = [str(row.get("component_type") or "") for row in components]
        if len(components) != 3 or sorted(component_types) != ["ESTIMATED_CIT", "REAL_COST", "REVENUE"]:
            _row_result_error(
                failures,
                ledger_id,
                f"components must be exactly one REVENUE, REAL_COST and ESTIMATED_CIT; got {component_types}",
                errors,
            )
        by_type = {str(row["component_type"]): row for row in components}
        if set(by_type) == {"REVENUE", "REAL_COST", "ESTIMATED_CIT"}:
            if not _same_decimal(by_type["REVENUE"].get("amount"), raw_ledger.get("revenue")):
                _row_result_error(failures, ledger_id, "REVENUE component does not reconcile", errors)
            if not _same_decimal(by_type["REAL_COST"].get("amount"), raw_ledger.get("real_cost")):
                _row_result_error(failures, ledger_id, "REAL_COST component does not reconcile", errors)
            if not _same_decimal(by_type["ESTIMATED_CIT"].get("amount"), raw_ledger.get("estimated_cit")):
                _row_result_error(failures, ledger_id, "ESTIMATED_CIT component does not reconcile", errors)
            if by_type["ESTIMATED_CIT"].get("management_input_id") is not None:
                _row_result_error(failures, ledger_id, "ESTIMATED_CIT component must not reference management input", errors)

        scope_inputs = inputs_by_scope.get(scope, [])
        selected_ids: dict[str, int] = {}
        selected_views: tuple[EntityTaxManagementInputView, ...] = ()
        try:
            selected_views = select_latest_reviewed_management_inputs(
                [_input_view(row) for row in scope_inputs],
                reporting_party_id=party,
                tax_period=period,
            )
            selected_ids = {
                row.input_type: int(row.id)
                for row in selected_views
                if row.id is not None
            }
        except Exception as exc:  # evidence errors are a gate failure, not a crash
            _row_result_error(failures, ledger_id, f"management input evidence is unavailable: {exc}", errors)

        for input_type in ("REVENUE", "REAL_COST"):
            component = by_type.get(input_type)
            if component is None:
                continue
            management_id = component.get("management_input_id")
            if management_id is None or selected_ids.get(input_type) != int(management_id):
                _row_result_error(
                    failures,
                    ledger_id,
                    f"{input_type} component does not point to the latest reviewed explicit-month input",
                    errors,
                )

        # A VAT failure or a component failure should not cause the gate to
        # stop before recording the CIT/input-hash result mismatch.
        if vat_ledger is None or vat_run is None or not selected_views or not rule_views:
            continue

        matches: list[tuple[str, Any]] = []
        for code in sorted({row.code for row in rule_views if row.code}):
            try:
                calculation = calculate_entity_tax_ledger(
                    reporting_party_id=party,
                    tax_period=period,
                    management_inputs=[_input_view(row) for row in scope_inputs],
                    tax_rules=rule_views,
                    vat_ledger=_vat_view(vat_ledger, vat_run)[0],
                    vat_calculation_run=_vat_view(vat_ledger, vat_run)[1],
                    entity={
                        "party_id": party,
                        "legal_entity": entity.get("legal_entity"),
                    },
                    cit_rule_code=code,
                )
            except (EntityTaxLedgerError, EntityTaxEvidenceError, ValueError, TypeError):
                continue
            if (
                calculation.input_snapshot_sha256 == str(run.get("input_snapshot_sha256"))
                and calculation.result_sha256 == str(run.get("result_sha256"))
            ):
                matches.append((code, calculation))

        if len(matches) != 1:
            _row_result_error(
                failures,
                ledger_id,
                f"no unique reviewed/effective CIT rule matches the input/result hashes (matches={len(matches)})",
                errors,
            )
            continue

        code, calculation = matches[0]
        tax_rule_matches[ledger_id] = {
            "code": code,
            "id": calculation.input_snapshot.get("cit_rule", {}).get("id"),
            "rate": calculation.input_snapshot.get("cit_rule", {}).get("rate"),
            "reviewed": calculation.input_snapshot.get("cit_rule", {}).get("reviewed"),
            "effective_from": calculation.input_snapshot.get("cit_rule", {}).get("effective_from"),
            "effective_to": calculation.input_snapshot.get("cit_rule", {}).get("effective_to"),
        }
        for field in ("revenue", "real_cost", "estimated_profit", "estimated_cit"):
            if not _same_decimal(raw_ledger.get(field), getattr(calculation, field)):
                _row_result_error(failures, ledger_id, f"deterministic {field} recomputation differs", errors)
        if str(raw_ledger.get("result_sha256")) != calculation.result_sha256:
            _row_result_error(failures, ledger_id, "result hash differs from deterministic V3 calculation", errors)
        if str(raw_ledger.get("input_snapshot_sha256")) != calculation.input_snapshot_sha256:
            _row_result_error(failures, ledger_id, "input snapshot hash differs from deterministic V3 calculation", errors)

    # A ledger that is not part of one current-state chain is an orphan or an
    # incorrect duplicate current.  Historical rows are accepted only when
    # directly reachable from the CLOSED anchor.
    for ledger in ledger_rows:
        ledger_id = int(ledger["id"])
        scope = (int(ledger["reporting_party_id"]), ledger["tax_period"])
        run_id = int(ledger["calculation_run_id"])
        if run_id not in chain_run_ids_by_scope.get(scope, set()):
            _row_result_error(
                failures,
                ledger_id,
                "ledger run is not reachable from the current TaxPeriodState chain",
                errors,
            )

    valid_current_ids = sorted(candidate_current_ids - errors)
    return {
        "entity_tax_ledger_count": len(ledger_rows),
        "official_entity_tax_ledger_ids": valid_current_ids,
        "candidate_current_entity_tax_ledger_ids": sorted(candidate_current_ids),
        "invalid_entity_tax_ledger_ids": sorted(errors),
        "tax_rule_matches": {str(key): value for key, value in sorted(tax_rule_matches.items())},
        "entity_tax_chain_run_ids": {
            f"{party}:{period}": sorted(run_ids)
            for (party, period), run_ids in sorted(chain_run_ids_by_scope.items(), key=repr)
        },
        "entity_tax_chain_errors": chain_errors,
    }


def _vat_ledgers_for_scope(conn: Connection, party: int, period: Any) -> list[dict[str, Any]]:
    """Read only VAT ledgers needed by a Task15 scope check."""

    return [
        dict(row)
        for row in conn.execute(
            text(
                """
                SELECT *
                FROM entity_vat_ledgers
                WHERE reporting_party_id = :party
                  AND tax_period = :period
                """
            ),
            {"party": party, "period": period},
        ).mappings()
    ]


def _run_read_only(database_url: str) -> dict[str, Any]:
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    failures: list[str] = []
    evidence: dict[str, Any] = {"database": make_url(database_url).database}
    try:
        with engine.connect() as conn:
            # Explicit read-only transaction is a hard gate property.  The
            # verifier never commits this transaction; rollback is mandatory.
            conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                inspector = inspect(conn)
                tables = set(inspector.get_table_names(schema="public"))
                db_heads = _db_heads(conn, inspector)
                try:
                    disk_heads = _disk_heads()
                except Exception as exc:
                    disk_heads = set()
                    failures.append(f"unable to read Tax Alembic disk head: {exc}")

                evidence.update(
                    {
                        "alembic_db_heads": sorted(db_heads),
                        "alembic_disk_heads": sorted(disk_heads),
                        "read_only_transaction": True,
                        "database_tables": sorted(tables),
                    }
                )
                if db_heads != {EXPECTED_HEAD}:
                    failures.append(f"database head must be {EXPECTED_HEAD}: {sorted(db_heads)}")
                if disk_heads != {EXPECTED_HEAD}:
                    failures.append(f"disk head must be {EXPECTED_HEAD}: {sorted(disk_heads)}")

                evidence["schema"] = _schema_contract(conn, inspector, failures)

                # Revision 86 deliberately has none of the Task15 tables.  Do
                # not issue SELECTs against absent tables; return a clear
                # BLOCKED result below instead of an unhandled UndefinedTable.
                if db_heads != {EXPECTED_HEAD}:
                    evidence.update(
                        {
                            "entity_tax_ledger_count": 0,
                            "official_entity_tax_ledger_ids": [],
                            "pilot_present": False,
                            "blocked_reason": "Task15 revision 87 is not the database head",
                        }
                    )
                elif not TASK15_TABLES.issubset(tables):
                    evidence.update(
                        {
                            "entity_tax_ledger_count": 0,
                            "official_entity_tax_ledger_ids": [],
                            "pilot_present": False,
                            "blocked_reason": "revision 87 database is missing one or more Task15 tables",
                        }
                    )
                else:
                    # All Task15 and base tables required by the read model
                    # should exist after revision 87.  Missing base tables are
                    # reported as FAIL without allowing a probe to crash.
                    base_tables = {
                        "internal_entities",
                        "calculation_runs",
                        "tax_period_states",
                        "entity_vat_ledgers",
                        "tax_rules",
                    }
                    missing_base = sorted(base_tables - tables)
                    if missing_base:
                        failures.append(f"missing base tables required by Gate S15: {missing_base}")
                    else:
                        entity_rows = _table_rows(conn, "internal_entities")
                        run_rows = _table_rows(conn, "calculation_runs")
                        state_rows = _table_rows(conn, "tax_period_states")
                        ledger_rows = _table_rows(conn, "entity_tax_ledgers")
                        component_rows = _table_rows(conn, "entity_tax_ledger_components")
                        input_rows = _table_rows(conn, "entity_tax_management_inputs")
                        rule_rows = _table_rows(conn, "tax_rules")

                        internal_entities = {
                            int(row["party_id"]): row for row in entity_rows
                        }
                        runs = {int(row["id"]): row for row in run_rows}
                        states = {
                            (int(row["reporting_party_id"]), str(row["tax_type"]), row["tax_period"]): row
                            for row in state_rows
                        }
                        inputs_by_scope: dict[tuple[int, Any], list[Mapping[str, Any]]] = defaultdict(list)
                        for row in input_rows:
                            inputs_by_scope[(int(row["reporting_party_id"]), row["tax_period"])].append(row)
                        components_by_ledger: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
                        for row in component_rows:
                            components_by_ledger[int(row["ledger_id"])].append(row)

                        ledger_evidence = _check_entity_tax_ledgers(
                            conn=conn,
                            failures=failures,
                            internal_entities=internal_entities,
                            runs=runs,
                            states=states,
                            tax_ledgers=ledger_rows,
                            components_by_ledger=components_by_ledger,
                            inputs_by_scope=inputs_by_scope,
                            tax_rules=rule_rows,
                        )
                        evidence.update(ledger_evidence)
                        evidence.update(
                            {
                                "calculation_run_count": len(run_rows),
                                "entity_tax_period_state_count": sum(
                                    1 for row in state_rows if str(row.get("tax_type")) == ENTITY_TAX_TYPE
                                ),
                                "management_input_count": len(input_rows),
                                "tax_rule_count": len(rule_rows),
                            }
                        )

                        official_ids = evidence.get("official_entity_tax_ledger_ids") or []
                        if not official_ids:
                            failures.append(
                                "Task15 requires at least one formal legal-entity/month EntityTaxLedger pilot; empty or invalid evidence cannot PASS"
                            )
                        evidence["pilot_present"] = bool(official_ids)
            finally:
                conn.exec_driver_sql("ROLLBACK")
    except Exception as exc:
        # A Gate artifact must remain machine-readable even for a connection or
        # catalog failure.  This is a blocked precondition, not a PASS.
        evidence.setdefault("read_only_transaction", False)
        evidence["unhandled_error"] = type(exc).__name__
        failures.append(f"Gate S15 could not complete its read-only inspection: {exc}")
        return {"status": "BLOCKED", "failures": failures, "evidence": evidence}
    finally:
        engine.dispose()

    db_ready = evidence.get("alembic_db_heads") == [EXPECTED_HEAD]
    disk_ready = evidence.get("alembic_disk_heads") == [EXPECTED_HEAD]
    if not db_ready or not disk_ready:
        status = "BLOCKED"
    else:
        status = "PASS" if not failures else "FAIL"
    return {"status": status, "failures": failures, "evidence": evidence}


def run() -> dict[str, Any]:
    """Run Gate S15 and always return a JSON-serializable result mapping."""

    try:
        database_url = _database_url()
    except Exception as exc:
        return {
            "status": "BLOCKED",
            "failures": [str(exc)],
            "evidence": {"read_only_transaction": False},
        }
    return _run_read_only(database_url)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path", help="write Gate S15 JSON evidence")
    args = parser.parse_args()
    result = run()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    if result["status"] == "PASS":
        return 0
    if result["status"] == "BLOCKED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
