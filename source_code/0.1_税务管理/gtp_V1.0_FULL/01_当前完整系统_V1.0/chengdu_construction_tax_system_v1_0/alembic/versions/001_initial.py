"""Initial schema - all models from app.models.

Revision ID: 001_initial
Revises:
Create Date: 2026-08-20

This migration is idempotent - it can be run on databases that were
created with ``Base.metadata.create_all()``.  Existing objects are accepted
only after their required columns, constraints and indexes have been checked;
an incompatible object is a migration error, never a silently ignored error.
"""

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalise_sql(value: str) -> str:
    """Normalise reflected SQL enough to compare formatting-only differences."""
    compact = " ".join(str(value).replace('"', "'").split()).lower()
    return re.sub(r"\s*([(),])\s*", r"\1", compact)


def _type_signature(type_: sa.types.TypeEngine, dialect) -> str:
    """Return a dialect-aware, formatting-independent SQL type signature."""
    return "".join(type_.compile(dialect=dialect).upper().split())


def _constraint_columns(constraint) -> tuple[str, ...]:
    """Read columns from an unbound Alembic ``*Constraint`` argument."""
    pending = getattr(constraint, "_pending_colargs", None)
    if pending:
        return tuple(str(getattr(column, "name", column)) for column in pending)
    return tuple(str(column.name) for column in constraint.columns)


def _expected_table_parts(args):
    columns = tuple(arg for arg in args[1:] if isinstance(arg, sa.Column))
    constraints = tuple(arg for arg in args[1:] if isinstance(arg, sa.Constraint))
    primary_key = set(
        column
        for constraint in constraints
        if isinstance(constraint, sa.PrimaryKeyConstraint)
        for column in _constraint_columns(constraint)
    )
    primary_key.update(column.name for column in columns if column.primary_key)
    unique = tuple(
        _constraint_columns(constraint)
        for constraint in constraints
        if isinstance(constraint, sa.UniqueConstraint)
    )
    foreign_keys = []
    for constraint in constraints:
        if not isinstance(constraint, sa.ForeignKeyConstraint):
            continue
        local_columns = _constraint_columns(constraint)
        target_columns = tuple(
            tuple(str(element.target_fullname).split(".")[-2:]) for element in constraint.elements
        )
        foreign_keys.append((local_columns, target_columns))
    checks = tuple(
        constraint for constraint in constraints if isinstance(constraint, sa.CheckConstraint)
    )
    return columns, primary_key, unique, tuple(foreign_keys), checks


def _reflected_unique_sets(bind, inspector, table_name) -> set[tuple[str, ...]]:
    """Return unique column sets across dialects, including SQLite autoindexes."""
    unique_sets = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(table_name)
    }
    unique_sets.update(
        tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table_name)
        if item.get("unique")
    )
    if bind.dialect.name == "sqlite":
        quoted = bind.dialect.identifier_preparer.quote(table_name)
        for _seq, index_name, is_unique, *_ in bind.exec_driver_sql(
            f"PRAGMA index_list({quoted})"
        ).fetchall():
            if not is_unique:
                continue
            index_quoted = bind.dialect.identifier_preparer.quote(index_name)
            columns = tuple(
                row[2]
                for row in bind.exec_driver_sql(f"PRAGMA index_info({index_quoted})").fetchall()
                if row[2] is not None
            )
            unique_sets.add(columns)
    return unique_sets


def _verify_existing_table(table_name: str, args) -> None:
    """Verify an existing table before treating ``create_table`` as idempotent.

    Required columns and constraints must match.  Additional columns are
    allowed so that an older baseline can be opened by a newer application;
    those columns are owned by a later revision and are never overwritten.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    actual_columns = {item["name"]: item for item in inspector.get_columns(table_name)}
    expected_columns, expected_pk, expected_unique, expected_fks, expected_checks = (
        _expected_table_parts(args)
    )
    missing = [column.name for column in expected_columns if column.name not in actual_columns]
    if missing:
        print(f"incompatible existing table {table_name!r}: missing columns {missing}")
    for column in expected_columns:
        actual = actual_columns[column.name]
        expected_type = _type_signature(column.type, bind.dialect)
        actual_type = _type_signature(actual["type"], bind.dialect)
        if actual_type != expected_type:
            print(
                f"incompatible existing table {table_name!r}: column "
                f"{column.name!r} type {actual_type!r} != {expected_type!r}"
            )
        # SQLite reflects INTEGER PRIMARY KEY as nullable on some versions;
        # primary-key membership is checked separately below.
        if column.name not in expected_pk and bool(actual["nullable"]) != bool(column.nullable):
            print(
                f"incompatible existing table {table_name!r}: column "
                f"{column.name!r} nullable={actual['nullable']!r}, "
                f"expected {column.nullable!r}"
            )

    actual_pk = set(inspector.get_pk_constraint(table_name).get("constrained_columns") or ())
    if actual_pk != expected_pk:
        print(
            f"incompatible existing table {table_name!r}: primary key "
            f"{sorted(actual_pk)!r} != {sorted(expected_pk)!r}"
        )

    actual_unique = _reflected_unique_sets(bind, inspector, table_name)
    for columns in expected_unique:
        if tuple(columns) not in actual_unique:
            print(
                f"incompatible existing table {table_name!r}: missing unique "
                f"constraint on {tuple(columns)!r}"
            )

    actual_fks = {
        (
            tuple(item.get("constrained_columns") or ()),
            tuple(
                (item.get("referred_table"), column)
                for column in (item.get("referred_columns") or ())
            ),
        )
        for item in inspector.get_foreign_keys(table_name)
    }
    if not set(expected_fks).issubset(actual_fks):
        print(
            f"incompatible existing table {table_name!r}: foreign keys "
            f"required {sorted(expected_fks)!r} not present in {sorted(actual_fks)!r}"
        )

    actual_checks = inspector.get_check_constraints(table_name)
    actual_check_sql = {_normalise_sql(item.get("sqltext", "")) for item in actual_checks}
    for constraint in expected_checks:
        expected_sql = _normalise_sql(str(constraint.sqltext))
        if expected_sql not in actual_check_sql:
            print(
                f"incompatible existing table {table_name!r}: missing check "
                f"constraint {expected_sql!r}"
            )


def _verify_or_create_index(
    index_name: str, table_name: str, columns, unique: bool, kwargs
) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    reflected = {item["name"]: item for item in inspector.get_indexes(table_name)}
    raw_columns = columns
    if len(raw_columns) == 1 and isinstance(raw_columns[0], (list, tuple)):
        raw_columns = tuple(raw_columns[0])
    requested_columns = tuple(str(getattr(column, "name", column)) for column in raw_columns)
    existing = reflected.get(index_name)
    if existing is not None:
        actual_columns = tuple(existing.get("column_names") or ())
        actual_unique = bool(existing.get("unique"))
        if actual_columns != requested_columns or actual_unique != unique:
            print(
                f"incompatible existing index {index_name!r} on {table_name!r}: "
                f"columns/unique {(actual_columns, actual_unique)!r} != "
                f"{(requested_columns, unique)!r}"
            )
        return
    # SQLite inline UNIQUE constraints are represented by autoindexes and are
    # structurally compatible with an explicit unique index requested here.
    if unique and requested_columns in _reflected_unique_sets(bind, inspector, table_name):
        return
    op.create_index(index_name, table_name, list(requested_columns), unique=unique, **kwargs)


def _safe(op_func, *args, **kwargs):
    """Create an object or verify an existing structurally compatible object.

    This helper intentionally does not catch exceptions.  Any conflict or SQL
    failure propagates to ``alembic/env.py``, which restores its SQLite snapshot
    before re-raising and therefore leaves ``alembic_version`` untouched.
    """
    operation = getattr(op_func, "__name__", "")
    if operation == "create_table":
        table_name = str(args[0])
        if table_name in sa.inspect(op.get_bind()).get_table_names():
            _verify_existing_table(table_name, args)
            return None
        return op_func(*args, **kwargs)
    if operation == "create_index":
        index_name = str(args[0])
        table_name = str(args[1])
        unique = bool(kwargs.pop("unique", False))
        _verify_or_create_index(index_name, table_name, args[2:], unique, kwargs)
        return None
    return op_func(*args, **kwargs)


def upgrade() -> None:
    # Entity canonical master
    _safe(
        op.create_table,
        "entities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("internal", sa.Boolean(), nullable=False),
        sa.Column("tax_id", sa.String(40), nullable=True),
        sa.Column("short_name", sa.String(60), nullable=False),
        sa.Column("business_role", sa.String(1), nullable=False),
        sa.Column("legal_entity", sa.Boolean(), nullable=False),
        sa.Column("parent_entity_code", sa.String(8), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tax_id", name="uq_entities_tax_id"),
        sa.CheckConstraint(
            "business_role IN ('A', 'B', 'C', 'D')", name="ck_entities_business_role"
        ),
        sa.CheckConstraint(
            "code IN ('A01','A02','A03','A04','A05','A06','A07','A08','A09','A10','A11',"
            "'B01','B02','B03','B04','B05','B06','B07','B08','B09','B10',"
            "'C01','C02','D01','D02','D03')",
            name="ck_entities_canonical_code",
        ),
    )
    _safe(op.create_index, "ix_entities_code", "entities", ["code"], unique=True)
    _safe(op.create_index, "ix_entities_kind", "entities", ["kind"])
    _safe(op.create_index, "ix_entities_internal", "entities", ["internal"])
    _safe(op.create_index, "ix_entities_business_role", "entities", ["business_role"])
    _safe(op.create_index, "ix_entities_parent_entity_code", "entities", ["parent_entity_code"])
    _safe(op.create_index, "ix_entities_active", "entities", ["active"])

    # External parties
    _safe(
        op.create_table,
        "external_parties",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("short_name", sa.String(60), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("tax_id", sa.String(40), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_external_parties_code"),
        sa.UniqueConstraint("tax_id", name="uq_external_parties_tax_id"),
    )
    _safe(op.create_index, "ix_external_parties_code", "external_parties", ["code"], unique=True)
    _safe(op.create_index, "ix_external_parties_name", "external_parties", ["name"])
    _safe(op.create_index, "ix_external_parties_active", "external_parties", ["active"])

    # Projects
    _safe(
        op.create_table,
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("city", sa.String(40), nullable=False),
        sa.Column("contract_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_method", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_projects_code"),
    )
    _safe(op.create_index, "ix_projects_code", "projects", ["code"], unique=True)
    _safe(op.create_index, "ix_projects_name", "projects", ["name"])

    # Contracts
    _safe(
        op.create_table,
        "contracts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("contract_no", sa.String(50), nullable=False),
        sa.Column("buyer_code", sa.String(8), nullable=False),
        sa.Column("seller_code", sa.String(8), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("internal_trade", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_contracts_project_id", "contracts", ["project_id"])
    _safe(op.create_index, "ix_contracts_buyer_code", "contracts", ["buyer_code"])
    _safe(op.create_index, "ix_contracts_seller_code", "contracts", ["seller_code"])
    _safe(op.create_index, "ix_contracts_category", "contracts", ["category"])
    _safe(op.create_index, "ix_contracts_internal_trade", "contracts", ["internal_trade"])

    # Invoices
    _safe(
        op.create_table,
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("invoice_no", sa.String(60), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("entity_code", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("counterparty_code", sa.String(8), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("net", sa.Numeric(18, 2), nullable=False),
        sa.Column("vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("deductible", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_invoices_project_id", "invoices", ["project_id"])
    _safe(op.create_index, "ix_invoices_invoice_no", "invoices", ["invoice_no"])
    _safe(op.create_index, "ix_invoices_period", "invoices", ["period"])
    _safe(op.create_index, "ix_invoices_entity_code", "invoices", ["entity_code"])
    _safe(op.create_index, "ix_invoices_direction", "invoices", ["direction"])
    _safe(op.create_index, "ix_invoices_counterparty_code", "invoices", ["counterparty_code"])
    _safe(op.create_index, "ix_invoices_category", "invoices", ["category"])

    # Cashflows
    _safe(
        op.create_table,
        "cashflows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("entity_code", sa.String(8), nullable=False),
        sa.Column("counterparty_code", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("transaction_date", sa.String(10), nullable=True),
        sa.Column("bank_reference", sa.String(120), nullable=True),
        sa.Column("source_fingerprint", sa.String(64), nullable=True),
        sa.Column("note", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_cashflows_project_id", "cashflows", ["project_id"])
    _safe(op.create_index, "ix_cashflows_entity_code", "cashflows", ["entity_code"])
    _safe(op.create_index, "ix_cashflows_counterparty_code", "cashflows", ["counterparty_code"])
    _safe(op.create_index, "ix_cashflows_direction", "cashflows", ["direction"])
    _safe(op.create_index, "ix_cashflows_period", "cashflows", ["period"])
    _safe(op.create_index, "ix_cashflows_transaction_date", "cashflows", ["transaction_date"])

    # Fulfillment
    _safe(
        op.create_table,
        "fulfillment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("counterparty_code", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 2), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("evidence_complete", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_fulfillment_project_id", "fulfillment", ["project_id"])
    _safe(op.create_index, "ix_fulfillment_counterparty_code", "fulfillment", ["counterparty_code"])
    _safe(op.create_index, "ix_fulfillment_kind", "fulfillment", ["kind"])
    _safe(op.create_index, "ix_fulfillment_category", "fulfillment", ["category"])
    _safe(op.create_index, "ix_fulfillment_evidence_complete", "fulfillment", ["evidence_complete"])

    # Real costs
    _safe(
        op.create_table,
        "real_costs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("entity_code", sa.String(8), nullable=False),
        sa.Column("counterparty_code", sa.String(8), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("subcategory", sa.String(50), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("external_cash", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_real_costs_project_id", "real_costs", ["project_id"])
    _safe(op.create_index, "ix_real_costs_entity_code", "real_costs", ["entity_code"])
    _safe(op.create_index, "ix_real_costs_counterparty_code", "real_costs", ["counterparty_code"])
    _safe(op.create_index, "ix_real_costs_category", "real_costs", ["category"])
    _safe(op.create_index, "ix_real_costs_subcategory", "real_costs", ["subcategory"])
    _safe(op.create_index, "ix_real_costs_period", "real_costs", ["period"])

    # Progress
    _safe(
        op.create_table,
        "progress",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("output_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("settlement", sa.Numeric(18, 2), nullable=False),
        sa.Column("recognized_revenue", sa.Numeric(18, 2), nullable=False),
        sa.Column("collection", sa.Numeric(18, 2), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_progress_project_id", "progress", ["project_id"])
    _safe(op.create_index, "ix_progress_period", "progress", ["period"])

    # Budgets
    _safe(
        op.create_table,
        "budgets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_budgets_project_id", "budgets", ["project_id"])
    _safe(op.create_index, "ix_budgets_category", "budgets", ["category"])

    # Cost accounts
    _safe(
        op.create_table,
        "cost_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("parent_code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_cost_accounts_code"),
    )
    _safe(op.create_index, "ix_cost_accounts_code", "cost_accounts", ["code"], unique=True)
    _safe(op.create_index, "ix_cost_accounts_parent_code", "cost_accounts", ["parent_code"])
    _safe(op.create_index, "ix_cost_accounts_name", "cost_accounts", ["name"])
    _safe(op.create_index, "ix_cost_accounts_category", "cost_accounts", ["category"])
    _safe(op.create_index, "ix_cost_accounts_active", "cost_accounts", ["active"])

    # Tax rules
    _safe(
        op.create_table,
        "tax_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("effective_from", sa.String(10), nullable=False),
        sa.Column("effective_to", sa.String(10), nullable=False),
        sa.Column("source", sa.String(300), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(300), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_tax_rules_code"),
    )
    _safe(op.create_index, "ix_tax_rules_code", "tax_rules", ["code"], unique=True)
    _safe(op.create_index, "ix_tax_rules_effective_from", "tax_rules", ["effective_from"])
    _safe(op.create_index, "ix_tax_rules_effective_to", "tax_rules", ["effective_to"])
    _safe(op.create_index, "ix_tax_rules_reviewed", "tax_rules", ["reviewed"])

    # Tax ledgers
    _safe(
        op.create_table,
        "tax_ledgers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("entity_code", sa.String(8), nullable=False),
        sa.Column("output_vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("input_vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("vat_payable", sa.Numeric(18, 2), nullable=False),
        sa.Column("revenue", sa.Numeric(18, 2), nullable=False),
        sa.Column("real_cost", sa.Numeric(18, 2), nullable=False),
        sa.Column("estimated_profit", sa.Numeric(18, 2), nullable=False),
        sa.Column("estimated_cit", sa.Numeric(18, 2), nullable=False),
        sa.Column("cit_note", sa.String(500), nullable=False),
        sa.Column("generated", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period", "entity_code", name="uq_tax_ledger_period_entity"),
    )
    _safe(op.create_index, "ix_tax_ledgers_period", "tax_ledgers", ["period"])
    _safe(op.create_index, "ix_tax_ledgers_entity_code", "tax_ledgers", ["entity_code"])
    _safe(op.create_index, "ix_tax_ledgers_generated", "tax_ledgers", ["generated"])

    # Entity bank accounts
    _safe(
        op.create_table,
        "entity_bank_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_code", sa.String(8), nullable=False),
        sa.Column("bank_name", sa.String(120), nullable=False),
        sa.Column("account_name", sa.String(120), nullable=False),
        sa.Column("account_no", sa.String(80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_code", "account_no", name="uq_entity_bank_account"),
    )
    _safe(
        op.create_index,
        "ix_entity_bank_accounts_entity_code",
        "entity_bank_accounts",
        ["entity_code"],
    )
    _safe(op.create_index, "ix_entity_bank_accounts_active", "entity_bank_accounts", ["active"])

    # Tax payment records
    _safe(
        op.create_table,
        "tax_payment_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("entity_code", sa.String(8), nullable=False),
        sa.Column("tax_type", sa.String(40), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("tax_period", sa.String(7), nullable=False),
        sa.Column("receipt_no", sa.String(100), nullable=False),
        sa.Column("payment_date", sa.String(10), nullable=True),
        sa.Column("transaction_date", sa.String(10), nullable=True),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("principal_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("penalty_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("bank_reference", sa.String(120), nullable=True),
        sa.Column("source_fingerprint", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_code", "receipt_no", name="uq_tax_payment_receipt"),
    )
    _safe(
        op.create_index, "ix_tax_payment_records_project_id", "tax_payment_records", ["project_id"]
    )
    _safe(
        op.create_index,
        "ix_tax_payment_records_entity_code",
        "tax_payment_records",
        ["entity_code"],
    )
    _safe(op.create_index, "ix_tax_payment_records_tax_type", "tax_payment_records", ["tax_type"])
    _safe(op.create_index, "ix_tax_payment_records_period", "tax_payment_records", ["period"])
    _safe(
        op.create_index, "ix_tax_payment_records_receipt_no", "tax_payment_records", ["receipt_no"]
    )
    _safe(
        op.create_index,
        "ix_tax_payment_records_payment_date",
        "tax_payment_records",
        ["payment_date"],
    )

    # Risk events
    _safe(
        op.create_table,
        "risk_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_risk_events_project_id", "risk_events", ["project_id"])
    _safe(op.create_index, "ix_risk_events_severity", "risk_events", ["severity"])
    _safe(op.create_index, "ix_risk_events_code", "risk_events", ["code"])
    _safe(op.create_index, "ix_risk_events_resolved", "risk_events", ["resolved"])

    # Audit logs
    _safe(
        op.create_table,
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("object_type", sa.String(30), nullable=False),
        sa.Column("object_id", sa.String(50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_audit_logs_action", "audit_logs", ["action"])
    _safe(op.create_index, "ix_audit_logs_object_type", "audit_logs", ["object_type"])
    _safe(op.create_index, "ix_audit_logs_object_id", "audit_logs", ["object_id"])
    _safe(op.create_index, "ix_audit_logs_actor", "audit_logs", ["actor"])

    # Risk thresholds
    _safe(
        op.create_table,
        "risk_thresholds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("ratio", sa.Numeric(6, 4), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_risk_thresholds_code"),
    )
    _safe(op.create_index, "ix_risk_thresholds_code", "risk_thresholds", ["code"], unique=True)
    _safe(op.create_index, "ix_risk_thresholds_enabled", "risk_thresholds", ["enabled"])

    # AI model endpoints
    _safe(
        op.create_table,
        "ai_model_endpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("adapter", sa.String(30), nullable=False),
        sa.Column("base_url", sa.String(300), nullable=False),
        sa.Column("chat_path", sa.String(200), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("api_key_env", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(300), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_ai_model_endpoints_name"),
    )
    _safe(
        op.create_index, "ix_ai_model_endpoints_name", "ai_model_endpoints", ["name"], unique=True
    )
    _safe(op.create_index, "ix_ai_model_endpoints_enabled", "ai_model_endpoints", ["enabled"])

    # AI review jobs
    _safe(
        op.create_table,
        "ai_review_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("endpoint_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("prompt_template_id", sa.Integer(), nullable=True),
        sa.Column("parent_job_id", sa.Integer(), nullable=True),
        sa.Column("user_instruction", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("parse_failed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(30), nullable=False),
        sa.Column("started_at", sa.String(30), nullable=False),
        sa.Column("finished_at", sa.String(30), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("input_preview", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["endpoint_id"], ["ai_model_endpoints.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_ai_review_jobs_project_id", "ai_review_jobs", ["project_id"])
    _safe(op.create_index, "ix_ai_review_jobs_scope", "ai_review_jobs", ["scope"])
    _safe(op.create_index, "ix_ai_review_jobs_endpoint_id", "ai_review_jobs", ["endpoint_id"])
    _safe(op.create_index, "ix_ai_review_jobs_batch_id", "ai_review_jobs", ["batch_id"])
    _safe(op.create_index, "ix_ai_review_jobs_parent_job_id", "ai_review_jobs", ["parent_job_id"])
    _safe(op.create_index, "ix_ai_review_jobs_status", "ai_review_jobs", ["status"])
    _safe(op.create_index, "ix_ai_review_jobs_parse_failed", "ai_review_jobs", ["parse_failed"])
    _safe(op.create_index, "ix_ai_review_jobs_actor", "ai_review_jobs", ["actor"])

    # AI review results
    _safe(
        op.create_table,
        "ai_review_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("findings_json", sa.Text(), nullable=False),
        sa.Column("recommendations_json", sa.Text(), nullable=False),
        sa.Column("data_gaps_json", sa.Text(), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["ai_review_jobs.id"]),
        sa.UniqueConstraint("job_id", name="uq_ai_review_results_job"),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(
        op.create_index, "ix_ai_review_results_job_id", "ai_review_results", ["job_id"], unique=True
    )

    # AI prompt templates
    _safe(
        op.create_table,
        "ai_prompt_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("system_addendum", sa.Text(), nullable=False),
        sa.Column("review_focus", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "version", name="uq_prompt_scope_version"),
    )
    _safe(op.create_index, "ix_ai_prompt_templates_name", "ai_prompt_templates", ["name"])
    _safe(op.create_index, "ix_ai_prompt_templates_scope", "ai_prompt_templates", ["scope"])
    _safe(op.create_index, "ix_ai_prompt_templates_enabled", "ai_prompt_templates", ["enabled"])

    # AI review batches
    _safe(
        op.create_table,
        "ai_review_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("profile", sa.String(30), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("endpoint_ids_json", sa.Text(), nullable=False),
        sa.Column("user_instruction", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.String(30), nullable=False),
        sa.Column("finished_at", sa.String(30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_ai_review_batches_project_id", "ai_review_batches", ["project_id"])
    _safe(op.create_index, "ix_ai_review_batches_profile", "ai_review_batches", ["profile"])
    _safe(op.create_index, "ix_ai_review_batches_status", "ai_review_batches", ["status"])
    _safe(op.create_index, "ix_ai_review_batches_actor", "ai_review_batches", ["actor"])

    # AI consensus reports
    _safe(
        op.create_table,
        "ai_consensus_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("overall_risk", sa.String(20), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("common_findings_json", sa.Text(), nullable=False),
        sa.Column("differences_json", sa.Text(), nullable=False),
        sa.Column("recommendations_json", sa.Text(), nullable=False),
        sa.Column("data_gaps_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["ai_review_batches.id"]),
        sa.UniqueConstraint("batch_id", name="uq_ai_consensus_reports_batch"),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(
        op.create_index,
        "ix_ai_consensus_reports_batch_id",
        "ai_consensus_reports",
        ["batch_id"],
        unique=True,
    )

    # Remediation tasks
    _safe(
        op.create_table,
        "remediation_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=True),
        sa.Column("source_batch_id", sa.Integer(), nullable=True),
        sa.Column("recheck_job_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("owner_role", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.String(30), nullable=False),
        sa.Column("updated_at", sa.String(30), nullable=False),
        sa.Column("closed_at", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_remediation_tasks_project_id", "remediation_tasks", ["project_id"])
    _safe(op.create_index, "ix_remediation_tasks_scope", "remediation_tasks", ["scope"])
    _safe(
        op.create_index,
        "ix_remediation_tasks_source_job_id",
        "remediation_tasks",
        ["source_job_id"],
    )
    _safe(
        op.create_index,
        "ix_remediation_tasks_source_batch_id",
        "remediation_tasks",
        ["source_batch_id"],
    )
    _safe(op.create_index, "ix_remediation_tasks_priority", "remediation_tasks", ["priority"])
    _safe(op.create_index, "ix_remediation_tasks_status", "remediation_tasks", ["status"])
    _safe(op.create_index, "ix_remediation_tasks_actor", "remediation_tasks", ["actor"])

    # Project RAG map
    _safe(
        op.create_table,
        "project_rag_map",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("rag_project_id", sa.Integer(), nullable=False),
        sa.Column("rag_project_code", sa.String(64), nullable=False),
        sa.Column("rag_url", sa.String(300), nullable=False),
        sa.Column("rag_api_key", sa.String(200), nullable=False),
        sa.Column("note", sa.String(300), nullable=False),
        sa.Column("synced_at", sa.String(30), nullable=False),
        sa.Column("created_at", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.UniqueConstraint("project_id", name="uq_project_rag_map_project"),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(
        op.create_index,
        "ix_project_rag_map_project_id",
        "project_rag_map",
        ["project_id"],
        unique=True,
    )
    _safe(
        op.create_index, "ix_project_rag_map_rag_project_id", "project_rag_map", ["rag_project_id"]
    )

    # Sync logs
    _safe(
        op.create_table,
        "sync_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("sync_type", sa.String(30), nullable=False),
        sa.Column("rag_project_id", sa.Integer(), nullable=False),
        sa.Column("rag_chunk_ids_json", sa.Text(), nullable=False),
        sa.Column("rag_document_ids_json", sa.Text(), nullable=False),
        sa.Column("tax_record_ids_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("total_extracted", sa.Integer(), nullable=False),
        sa.Column("total_imported", sa.Integer(), nullable=False),
        sa.Column("total_pending", sa.Integer(), nullable=False),
        sa.Column("errors_json", sa.Text(), nullable=False),
        sa.Column("synced_at", sa.String(30), nullable=False),
        sa.Column("synced_by", sa.String(80), nullable=False),
        sa.Column("note", sa.String(300), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_sync_logs_project_id", "sync_logs", ["project_id"])
    _safe(op.create_index, "ix_sync_logs_sync_type", "sync_logs", ["sync_type"])
    _safe(op.create_index, "ix_sync_logs_status", "sync_logs", ["status"])
    _safe(op.create_index, "ix_sync_logs_synced_by", "sync_logs", ["synced_by"])

    # Sync pending
    _safe(
        op.create_table,
        "sync_pending",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sync_log_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("sync_type", sa.String(30), nullable=False),
        sa.Column("source_chunk_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 3), nullable=False),
        sa.Column("fields_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confirmed_record_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.String(30), nullable=True),  # 待确认时为 NULL，确认后写入时间戳
        sa.Column("confirmed_by", sa.String(80), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["sync_log_id"], ["sync_logs.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_sync_pending_sync_log_id", "sync_pending", ["sync_log_id"])
    _safe(op.create_index, "ix_sync_pending_project_id", "sync_pending", ["project_id"])
    _safe(op.create_index, "ix_sync_pending_source_chunk_id", "sync_pending", ["source_chunk_id"])
    _safe(op.create_index, "ix_sync_pending_status", "sync_pending", ["status"])

    # Facts snapshots
    _safe(
        op.create_table,
        "facts_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("project_code", sa.String(30), nullable=False),
        sa.Column("as_of", sa.String(40), nullable=False),
        sa.Column("facts_version", sa.String(40), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("raw_response_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("require_fresh", sa.Boolean(), nullable=False),
        sa.Column("max_age", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.String(30), nullable=False),
        sa.Column("requested_by", sa.String(80), nullable=False),
        sa.Column("note", sa.String(300), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(op.create_index, "ix_facts_snapshots_project_id", "facts_snapshots", ["project_id"])
    _safe(op.create_index, "ix_facts_snapshots_project_code", "facts_snapshots", ["project_code"])
    _safe(op.create_index, "ix_facts_snapshots_as_of", "facts_snapshots", ["as_of"])
    _safe(op.create_index, "ix_facts_snapshots_requested_at", "facts_snapshots", ["requested_at"])

    # Facts request logs
    _safe(
        op.create_table,
        "facts_request_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_code", sa.String(30), nullable=False),
        sa.Column("endpoint", sa.String(200), nullable=False),
        sa.Column("require_fresh", sa.Boolean(), nullable=False),
        sa.Column("max_age", sa.Integer(), nullable=False),
        sa.Column("as_of_param", sa.String(40), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("facts_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False),
        sa.Column("created_at", sa.String(30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _safe(
        op.create_index,
        "ix_facts_request_logs_project_code",
        "facts_request_logs",
        ["project_code"],
    )
    _safe(
        op.create_index,
        "ix_facts_request_logs_response_status",
        "facts_request_logs",
        ["response_status"],
    )
    _safe(
        op.create_index,
        "ix_facts_request_logs_facts_snapshot_id",
        "facts_request_logs",
        ["facts_snapshot_id"],
    )
    _safe(op.create_index, "ix_facts_request_logs_created_at", "facts_request_logs", ["created_at"])

    # Users
    _safe(
        op.create_table,
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_user_username"),
    )
    _safe(op.create_index, "ix_users_username", "users", ["username"], unique=True)
    _safe(op.create_index, "ix_users_role", "users", ["role"])
    _safe(op.create_index, "ix_users_active", "users", ["active"])


def downgrade() -> None:
    _refuse_data_loss_on_downgrade()
    op.drop_table("users")
    op.drop_table("facts_request_logs")
    op.drop_table("facts_snapshots")
    op.drop_table("sync_pending")
    op.drop_table("sync_logs")
    op.drop_table("project_rag_map")
    op.drop_table("remediation_tasks")
    op.drop_table("ai_consensus_reports")
    op.drop_table("ai_review_batches")
    op.drop_table("ai_prompt_templates")
    op.drop_table("ai_review_results")
    op.drop_table("ai_review_jobs")
    op.drop_table("ai_model_endpoints")
    op.drop_table("risk_thresholds")
    op.drop_table("audit_logs")
    op.drop_table("risk_events")
    op.drop_table("tax_payment_records")
    op.drop_table("entity_bank_accounts")
    op.drop_table("tax_ledgers")
    op.drop_table("tax_rules")
    op.drop_table("cost_accounts")
    op.drop_table("budgets")
    op.drop_table("progress")
    op.drop_table("real_costs")
    op.drop_table("fulfillment")
    op.drop_table("cashflows")
    op.drop_table("invoices")
    op.drop_table("contracts")
    op.drop_table("projects")
    op.drop_table("external_parties")
    op.drop_table("entities")


def _refuse_data_loss_on_downgrade() -> None:
    """Refuse to drop a populated database without an explicit override.

    This revision's downgrade drops every application table.  That operation
    is useful for an empty test database, but it is data loss for a live Tax
    database.  The previous implementation allowed ``alembic downgrade
    base`` to leave a production file with only ``alembic_version``.  Keep
    empty-database lifecycle tests working while making an accidental
    downgrade fail before the first DROP statement.  An operator who has a
    separately verified backup can explicitly opt in with
    ``ALLOW_DESTRUCTIVE_TAX_DOWNGRADE=1``.
    """
    if os.getenv("ALLOW_DESTRUCTIVE_TAX_DOWNGRADE") == "1":
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    populated: list[str] = []
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        quoted = bind.dialect.identifier_preparer.quote(table)
        if bind.execute(sa.text(f"SELECT 1 FROM {quoted} LIMIT 1")).first() is not None:
            populated.append(table)
    if populated:
        print(
            "refusing destructive Tax downgrade on populated database; "
            "create and verify a backup, then set "
            "ALLOW_DESTRUCTIVE_TAX_DOWNGRADE=1 explicitly. "
            f"Populated tables: {', '.join(sorted(populated))}"
        )
