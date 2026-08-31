"""Gate S15 PostgreSQL fixture and fail-closed contract tests.

The PASS case uses a short-lived disposable PostgreSQL database so the gate is
observed through a real revision-87 catalog and a committed pilot.  Negative
cases exercise the same gate verifier against an in-memory row snapshot; they
do not mutate the formal database or the shared disposable test database.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.domain.tax.entity_tax_ledger import (
    RULESET_VERSION,
    EntityTaxManagementInputView,
    EntityTaxRuleView,
    OfficialVatLedgerView,
    calculate_entity_tax_ledger,
)
from scripts.v3 import entity_tax_gate_15 as gate


ROOT = Path(__file__).resolve().parents[1]
PERIOD = date(2026, 8, 1)
# PostgreSQL returns timestamptz in the connection/session timezone (the
# project test cluster is Asia/Shanghai).  Keeping the fixture in that
# canonical representation makes the independently recomputed snapshot hash
# identical to the builder's ORM snapshot.
REVIEWED_AT = datetime(2026, 8, 31, 20, 0, tzinfo=timezone(timedelta(hours=8)))


def _create_run(conn, *, party_id: int, tax_type: str, result_hash: str, input_hash: str) -> int:
    run_id = conn.execute(
        text(
            """
            INSERT INTO calculation_runs
              (reporting_party_id, tax_type, tax_period, run_kind, run_status,
               ruleset_version, input_snapshot_sha256, created_by)
            VALUES (:party, :tax_type, :period, 'STANDARD', 'DRAFT',
                    :ruleset, :input_hash, 'task15-gate-test')
            RETURNING id
            """
        ),
        {
            "party": party_id,
            "tax_type": tax_type,
            "period": PERIOD,
            "ruleset": RULESET_VERSION if tax_type == "ENTITY_TAX" else "V3_ENTITY_VAT_LEDGER_V1",
            "input_hash": input_hash,
        },
    ).scalar_one()
    conn.execute(
        text(
            """
            UPDATE calculation_runs
               SET run_status='SUCCEEDED', result_sha256=:result_hash,
                   completed_at=CURRENT_TIMESTAMP
             WHERE id=:run_id
            """
        ),
        {"run_id": run_id, "result_hash": result_hash},
    )
    return int(run_id)


def _seed_committed_pass_fixture(database_url: str) -> None:
    """Create one valid, committed Task15 pilot in a disposable database."""

    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        party_id = int(
            conn.execute(
                text(
                    """
                    INSERT INTO parties (code, name, short_name, party_type, active)
                    VALUES ('TASK15-GATE-PARTY', 'Task15 Gate Party', 'T15G', 'internal', true)
                    RETURNING id
                    """
                )
            ).scalar_one()
        )
        conn.execute(
            text(
                """
                INSERT INTO internal_entities
                  (party_id, canonical_code, business_role, legal_entity, active)
                VALUES (:party, 'A15GATE', 'A', true, true)
                """
            ),
            {"party": party_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO tax_rules
                  (code, rate, effective_from, effective_to, source, reviewed, note)
                VALUES ('CIT_GENERAL', 0.2500, '2026-01-01', '',
                        'Task15 reviewed gate fixture', true, 'fixture')
                """
            )
        )

        vat_input_hash = "a" * 64
        vat_result_hash = "b" * 64
        vat_run_id = _create_run(
            conn,
            party_id=party_id,
            tax_type="VAT",
            result_hash=vat_result_hash,
            input_hash=vat_input_hash,
        )
        conn.execute(
            text(
                """
                INSERT INTO vat_output_period_assertions
                  (reporting_party_id, tax_period, asserted_output_vat_total,
                   source, reviewed, reviewed_by, reviewed_at)
                VALUES (:party, :period, 0.00, 'Task15 reviewed gate fixture',
                        true, 'pytest', :reviewed_at)
                """
            ),
            {"party": party_id, "period": PERIOD, "reviewed_at": REVIEWED_AT},
        )
        conn.execute(
            text(
                """
                INSERT INTO tax_period_states
                  (reporting_party_id, tax_type, tax_period, state, current_run_id)
                VALUES (:party, 'VAT', :period, 'OPEN', :run_id)
                """
            ),
            {"party": party_id, "period": PERIOD, "run_id": vat_run_id},
        )
        vat_ledger_id = int(
            conn.execute(
                text(
                    """
                    INSERT INTO entity_vat_ledgers
                      (calculation_run_id, reporting_party_id, tax_period,
                       opening_input_credit, output_vat, input_vat, tax_prepayment,
                       vat_payable_before_prepayment, closing_input_credit,
                       vat_payable_after_prepayment, unapplied_tax_prepayment)
                    VALUES (:run_id, :party, :period, 0.00, 0.00, 0.00, 0.00,
                            0.00, 0.00, 0.00, 0.00)
                    RETURNING id
                    """
                ),
                {"run_id": vat_run_id, "party": party_id, "period": PERIOD},
            ).scalar_one()
        )

        revenue_id = int(
            conn.execute(
                text(
                    """
                    INSERT INTO entity_tax_management_inputs
                      (reporting_party_id, tax_period, input_type, input_version,
                       amount, source, reviewed, reviewed_by, reviewed_at)
                    VALUES (:party, :period, 'REVENUE', 1, 100.00,
                            'Task15 reviewed revenue fixture', true, 'pytest', :reviewed_at)
                    RETURNING id
                    """
                ),
                {"party": party_id, "period": PERIOD, "reviewed_at": REVIEWED_AT},
            ).scalar_one()
        )
        cost_id = int(
            conn.execute(
                text(
                    """
                    INSERT INTO entity_tax_management_inputs
                      (reporting_party_id, tax_period, input_type, input_version,
                       amount, source, reviewed, reviewed_by, reviewed_at)
                    VALUES (:party, :period, 'REAL_COST', 1, 40.00,
                            'Task15 reviewed cost fixture', true, 'pytest', :reviewed_at)
                    RETURNING id
                    """
                ),
                {"party": party_id, "period": PERIOD, "reviewed_at": REVIEWED_AT},
            ).scalar_one()
        )

        revenue = EntityTaxManagementInputView(
            id=revenue_id,
            reporting_party_id=party_id,
            tax_period=PERIOD,
            input_type="REVENUE",
            input_version=1,
            amount=Decimal("100.00"),
            reviewed=True,
            reviewed_by="pytest",
            reviewed_at=REVIEWED_AT,
            source="Task15 reviewed revenue fixture",
        )
        cost = EntityTaxManagementInputView(
            id=cost_id,
            reporting_party_id=party_id,
            tax_period=PERIOD,
            input_type="REAL_COST",
            input_version=1,
            amount=Decimal("40.00"),
            reviewed=True,
            reviewed_by="pytest",
            reviewed_at=REVIEWED_AT,
            source="Task15 reviewed cost fixture",
        )
        rule = EntityTaxRuleView(
            id=1,
            code="CIT_GENERAL",
            rate=Decimal("0.2500"),
            effective_from="2026-01-01",
            effective_to=None,
            reviewed=True,
            source="Task15 reviewed gate fixture",
            note="fixture",
        )
        vat_view = OfficialVatLedgerView(
            id=vat_ledger_id,
            calculation_run_id=vat_run_id,
            reporting_party_id=party_id,
            tax_period=PERIOD,
            tax_type="VAT",
            run_status="SUCCEEDED",
            result_sha256=vat_result_hash,
            official=True,
        )
        calculation = calculate_entity_tax_ledger(
            reporting_party_id=party_id,
            tax_period=PERIOD,
            management_inputs=(revenue, cost),
            tax_rules=(rule,),
            vat_ledger=vat_view,
            vat_calculation_run={
                "id": vat_run_id,
                "reporting_party_id": party_id,
                "tax_type": "VAT",
                "tax_period": PERIOD,
                "run_status": "SUCCEEDED",
                "result_sha256": vat_result_hash,
            },
            entity={"party_id": party_id, "legal_entity": True},
        )
        entity_run_id = _create_run(
            conn,
            party_id=party_id,
            tax_type="ENTITY_TAX",
            result_hash=calculation.result_sha256,
            input_hash=calculation.input_snapshot_sha256,
        )
        conn.execute(
            text(
                """
                INSERT INTO tax_period_states
                  (reporting_party_id, tax_type, tax_period, state, current_run_id)
                VALUES (:party, 'ENTITY_TAX', :period, 'OPEN', :run_id)
                """
            ),
            {"party": party_id, "period": PERIOD, "run_id": entity_run_id},
        )
        entity_ledger_id = int(
            conn.execute(
                text(
                    """
                    INSERT INTO entity_tax_ledgers
                      (calculation_run_id, reporting_party_id, tax_period,
                       entity_vat_ledger_id, revenue, real_cost, estimated_profit,
                       estimated_cit, rule_version, input_snapshot_sha256, result_sha256)
                    VALUES (:run_id, :party, :period, :vat_id, :revenue, :cost,
                            :profit, :cit, :ruleset, :input_hash, :result_hash)
                    RETURNING id
                    """
                ),
                {
                    "run_id": entity_run_id,
                    "party": party_id,
                    "period": PERIOD,
                    "vat_id": vat_ledger_id,
                    "revenue": calculation.revenue,
                    "cost": calculation.real_cost,
                    "profit": calculation.estimated_profit,
                    "cit": calculation.estimated_cit,
                    "ruleset": RULESET_VERSION,
                    "input_hash": calculation.input_snapshot_sha256,
                    "result_hash": calculation.result_sha256,
                },
            ).scalar_one()
        )
        conn.execute(
            text(
                """
                INSERT INTO entity_tax_ledger_components
                  (ledger_id, component_type, amount, management_input_id)
                VALUES (:ledger_id, 'REVENUE', :revenue, :revenue_id),
                       (:ledger_id, 'REAL_COST', :cost, :cost_id),
                       (:ledger_id, 'ESTIMATED_CIT', :cit, NULL)
                """
            ),
            {
                "ledger_id": entity_ledger_id,
                "revenue": calculation.revenue,
                "revenue_id": revenue_id,
                "cost": calculation.real_cost,
                "cost_id": cost_id,
                "cit": calculation.estimated_cit,
            },
        )
    engine.dispose()


@pytest.fixture(scope="module")
def gate_pass_database(postgres_test_database_url: str):
    """Provision and remove a dedicated disposable PostgreSQL 87 database."""

    base = make_url(postgres_test_database_url)
    database_name = f"task15_gate_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin_url = base.set(database="postgres")
    dedicated_url = base.set(database=database_name)
    admin_engine = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    created = False
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", str(dedicated_url))
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = str(dedicated_url)
        try:
            command.upgrade(cfg, "head")
        finally:
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url
        _seed_committed_pass_fixture(str(dedicated_url))
        yield str(dedicated_url)
    except Exception as exc:
        pytest.skip(f"dedicated PostgreSQL Gate S15 fixture unavailable: {exc}")
    finally:
        admin_engine.dispose()
        if created:
            cleanup_engine = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
            try:
                with cleanup_engine.connect() as conn:
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            finally:
                cleanup_engine.dispose()


@pytest.fixture(scope="module")
def gate_empty_database(postgres_test_database_url: str):
    """Provision and remove a dedicated empty revision-87 database.

    The no-pilot contract must not depend on the order of the other Task15
    modules.  In particular, builder tests intentionally create an official
    EntityTaxLedger in the shared seeded database, so running this assertion
    against that database would turn a valid isolation check into an order-
    dependent false failure.
    """

    base = make_url(postgres_test_database_url)
    database_name = f"task15_gate_empty_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin_url = base.set(database="postgres")
    dedicated_url = base.set(database=database_name)
    admin_engine = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    created = False
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", str(dedicated_url))
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = str(dedicated_url)
        try:
            command.upgrade(cfg, "head")
        finally:
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url
        yield str(dedicated_url)
    finally:
        admin_engine.dispose()
        if created:
            cleanup_engine = create_engine(
                admin_url, future=True, isolation_level="AUTOCOMMIT"
            )
            try:
                with cleanup_engine.connect() as conn:
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            finally:
                cleanup_engine.dispose()


def _memory_fixture() -> dict:
    """Return a valid row snapshot for fast negative-contract tests."""

    party = 101
    vat_run_id = 201
    vat_ledger_id = 301
    entity_run_id = 401
    entity_ledger_id = 501
    revenue_id = 601
    cost_id = 602
    vat_run = {
        "id": vat_run_id,
        "reporting_party_id": party,
        "tax_type": "VAT",
        "tax_period": PERIOD,
        "run_status": "SUCCEEDED",
        "run_kind": "STANDARD",
        "ruleset_version": "V3_ENTITY_VAT_LEDGER_V1",
        "input_snapshot_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "supersedes_run_id": None,
    }
    vat_ledger = {
        "id": vat_ledger_id,
        "calculation_run_id": vat_run_id,
        "reporting_party_id": party,
        "tax_period": PERIOD,
    }
    inputs = [
        {
            "id": revenue_id,
            "reporting_party_id": party,
            "tax_period": PERIOD,
            "input_type": "REVENUE",
            "input_version": 1,
            "amount": Decimal("100.00"),
            "reviewed": True,
            "reviewed_by": "pytest",
            "reviewed_at": REVIEWED_AT,
            "source": "reviewed revenue",
            "source_document_id": None,
            "note": None,
        },
        {
            "id": cost_id,
            "reporting_party_id": party,
            "tax_period": PERIOD,
            "input_type": "REAL_COST",
            "input_version": 1,
            "amount": Decimal("40.00"),
            "reviewed": True,
            "reviewed_by": "pytest",
            "reviewed_at": REVIEWED_AT,
            "source": "reviewed cost",
            "source_document_id": None,
            "note": None,
        },
    ]
    rules = [
        {
            "id": 701,
            "code": "CIT_GENERAL",
            "rate": Decimal("0.2500"),
            "effective_from": "2026-01-01",
            "effective_to": None,
            "reviewed": True,
            "source": "reviewed rule",
            "note": None,
        }
    ]
    calc = calculate_entity_tax_ledger(
        reporting_party_id=party,
        tax_period=PERIOD,
        management_inputs=[gate._input_view(row) for row in inputs],
        tax_rules=[gate._rule_view(row) for row in rules],
        vat_ledger=gate._vat_view(vat_ledger, vat_run)[0],
        vat_calculation_run=gate._vat_view(vat_ledger, vat_run)[1],
        entity={"party_id": party, "legal_entity": True},
    )
    entity_run = {
        "id": entity_run_id,
        "reporting_party_id": party,
        "tax_type": "ENTITY_TAX",
        "tax_period": PERIOD,
        "run_status": "SUCCEEDED",
        "run_kind": "STANDARD",
        "ruleset_version": RULESET_VERSION,
        "input_snapshot_sha256": calc.input_snapshot_sha256,
        "result_sha256": calc.result_sha256,
        "supersedes_run_id": None,
    }
    ledger = {
        "id": entity_ledger_id,
        "calculation_run_id": entity_run_id,
        "reporting_party_id": party,
        "tax_period": PERIOD,
        "entity_vat_ledger_id": vat_ledger_id,
        "revenue": calc.revenue,
        "real_cost": calc.real_cost,
        "estimated_profit": calc.estimated_profit,
        "estimated_cit": calc.estimated_cit,
        "rule_version": RULESET_VERSION,
        "input_snapshot_sha256": calc.input_snapshot_sha256,
        "result_sha256": calc.result_sha256,
    }
    components = [
        {"ledger_id": entity_ledger_id, "component_type": "REVENUE", "amount": Decimal("100.00"), "management_input_id": revenue_id},
        {"ledger_id": entity_ledger_id, "component_type": "REAL_COST", "amount": Decimal("40.00"), "management_input_id": cost_id},
        {"ledger_id": entity_ledger_id, "component_type": "ESTIMATED_CIT", "amount": Decimal("15.00"), "management_input_id": None},
    ]
    return {
        "internal_entities": {party: {"party_id": party, "legal_entity": True, "active": True}},
        "runs": {vat_run_id: vat_run, entity_run_id: entity_run},
        "states": {
            (party, "VAT", PERIOD): {"id": 801, "reporting_party_id": party, "tax_type": "VAT", "tax_period": PERIOD, "state": "OPEN", "current_run_id": vat_run_id, "closed_run_id": None, "closed_by": None, "closed_at": None},
            (party, "ENTITY_TAX", PERIOD): {"id": 802, "reporting_party_id": party, "tax_type": "ENTITY_TAX", "tax_period": PERIOD, "state": "OPEN", "current_run_id": entity_run_id, "closed_run_id": None, "closed_by": None, "closed_at": None},
        },
        "tax_ledgers": [ledger],
        "components_by_ledger": {entity_ledger_id: components},
        "inputs_by_scope": {(party, PERIOD): inputs},
        "tax_rules": rules,
        "vat_ledgers": [vat_ledger],
    }


def _check_memory(monkeypatch, fixture: dict) -> list[str]:
    monkeypatch.setattr(
        gate,
        "_vat_ledgers_for_scope",
        lambda _conn, _party, _period: fixture["vat_ledgers"],
    )
    failures: list[str] = []
    result = gate._check_entity_tax_ledgers(
        conn=object(),
        failures=failures,
        internal_entities=fixture["internal_entities"],
        runs=fixture["runs"],
        states=fixture["states"],
        tax_ledgers=fixture["tax_ledgers"],
        components_by_ledger=fixture["components_by_ledger"],
        inputs_by_scope=fixture["inputs_by_scope"],
        tax_rules=fixture["tax_rules"],
    )
    assert result["entity_tax_ledger_count"] == len(fixture["tax_ledgers"])
    return failures


def test_gate_passes_with_committed_disposable_postgresql_87_fixture(
    gate_pass_database: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DATABASE_URL", gate_pass_database)
    result = gate.run()
    assert result["status"] == "PASS", result
    assert result["failures"] == []
    assert result["evidence"]["pilot_present"] is True
    assert result["evidence"]["official_entity_tax_ledger_ids"]
    assert result["evidence"]["schema"]["missing_tables"] == []
    assert result["evidence"]["schema"]["missing_triggers"] == []


def test_gate_empty_revision_87_fails_closed_without_pilot(
    gate_empty_database: str, monkeypatch
):
    """A valid 87 catalog with no official pilot is not a PASS."""

    monkeypatch.setenv("DATABASE_URL", gate_empty_database)
    result = gate.run()
    assert result["status"] == "FAIL"
    assert result["evidence"]["pilot_present"] is False
    assert any("requires at least one" in item for item in result["failures"])


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("non_legal", "not legal_entity"),
        ("higher_unreviewed", "management input evidence"),
        ("ambiguous_input", "management input evidence"),
        ("component_mismatch", "REVENUE component does not reconcile"),
        ("cit_hash", "CIT rule matches"),
        ("vat_status", "VAT current run is not a SUCCEEDED"),
        ("closed_chain", "closed run is missing"),
    ],
)
def test_gate_key_negative_contracts_fail_closed(
    monkeypatch: pytest.MonkeyPatch, mutation: str, expected: str
):
    fixture = _memory_fixture()
    party = 101
    period = PERIOD
    ledger_id = 501
    if mutation == "non_legal":
        fixture["internal_entities"][party]["legal_entity"] = False
    elif mutation == "higher_unreviewed":
        fixture["inputs_by_scope"][(party, period)].append(
            {
                **fixture["inputs_by_scope"][(party, period)][0],
                "id": 603,
                "input_version": 2,
                "reviewed": False,
                "reviewed_by": None,
                "reviewed_at": None,
            }
        )
    elif mutation == "ambiguous_input":
        fixture["inputs_by_scope"][(party, period)].append(
            {**fixture["inputs_by_scope"][(party, period)][0], "id": 604}
        )
    elif mutation == "component_mismatch":
        fixture["components_by_ledger"][ledger_id][0]["amount"] = Decimal("99.00")
    elif mutation == "cit_hash":
        fixture["runs"][401]["result_sha256"] = "f" * 64
        fixture["tax_ledgers"][0]["result_sha256"] = "f" * 64
    elif mutation == "vat_status":
        fixture["runs"][201]["run_status"] = "DRAFT"
    elif mutation == "closed_chain":
        state = fixture["states"][(party, "ENTITY_TAX", period)]
        state.update({"state": "CLOSED", "closed_run_id": 999, "closed_by": "pytest", "closed_at": REVIEWED_AT})

    failures = _check_memory(monkeypatch, fixture)
    assert any(expected in item for item in failures), failures


def test_gate_schema_contract_constants_cover_revision_87():
    migration = (ROOT / "alembic" / "versions" / "87_v3_entity_tax_ledgers.py").read_text(encoding="utf-8")
    for table in gate.TASK15_TABLES:
        assert f'"{table}"' in migration
    for index_names in gate.REQUIRED_INDEXES.values():
        for name in index_names:
            assert name in migration
    for name in gate.REQUIRED_TRIGGERS:
        assert name in migration
    assert gate.PROHIBITED_COLUMNS == {"project_id", "entity_code", "invoice_date"}
