"""Add Task19 canonical PaymentFact and legacy CashFlow mapping.

Revision ID: 90_v3_payment_facts
Revises: 89_v3_group_penetration
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "90_v3_payment_facts"
down_revision = "89_v3_group_penetration"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.create_table(
        "payment_facts",
        sa.Column("fact_id", sa.Integer(), sa.ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("payer_party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payee_party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column("payer_account_id", sa.Integer(), nullable=True),
        sa.Column("payee_account_id", sa.Integer(), nullable=True),
        sa.Column("bank_reference", sa.String(120), nullable=True),
        sa.Column("settlement_method", sa.String(24), nullable=False, server_default="UNKNOWN"),
        sa.Column("payment_nature", sa.String(20), nullable=False, server_default="OTHER"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("payer_party_id <> payee_party_id", name="ck_payment_facts_distinct_parties"),
        sa.CheckConstraint("amount > 0", name="ck_payment_facts_amount_positive"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_payment_facts_currency"),
        sa.CheckConstraint("settlement_method IN ('BANK_TRANSFER','CASH','BILL','OFFSET','OTHER','UNKNOWN')", name="ck_payment_facts_settlement_method"),
        sa.CheckConstraint("payment_nature IN ('NORMAL','ADVANCE','DEPOSIT','GUARANTEE','REFUND','TAX','PAYROLL','OTHER')", name="ck_payment_facts_nature"),
    )
    op.create_index("ix_payment_facts_payer_date", "payment_facts", ["payer_party_id", "transaction_date"])
    op.create_index("ix_payment_facts_payee_date", "payment_facts", ["payee_party_id", "transaction_date"])
    op.create_index("ix_payment_facts_bank_reference", "payment_facts", ["bank_reference"])

    op.create_table(
        "legacy_cashflow_map",
        sa.Column("legacy_cashflow_id", sa.Integer(), sa.ForeignKey("cashflows.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("payment_fact_id", sa.Integer(), sa.ForeignKey("payment_facts.fact_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("mapped_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('MIGRATED','NEEDS_REVIEW','REJECTED')", name="ck_legacy_cashflow_map_status"),
        sa.CheckConstraint("(status='MIGRATED' AND payment_fact_id IS NOT NULL) OR (status<>'MIGRATED' AND payment_fact_id IS NULL)", name="ck_legacy_cashflow_map_link_semantics"),
    )
    op.create_index("ix_legacy_cashflow_map_payment_fact", "legacy_cashflow_map", ["payment_fact_id"])
    op.create_index("ix_legacy_cashflow_map_status", "legacy_cashflow_map", ["status"])

    op.add_column("group_penetration_components", sa.Column("payment_fact_id", sa.Integer(), sa.ForeignKey("payment_facts.fact_id", ondelete="RESTRICT"), nullable=True))
    op.drop_constraint("ck_group_penetration_components_type", "group_penetration_components", type_="check")
    op.drop_constraint("ck_group_penetration_components_typed_source", "group_penetration_components", type_="check")
    op.create_check_constraint("ck_group_penetration_components_type", "group_penetration_components", "component_type IN ('EXTERNAL_REVENUE','EXTERNAL_LEAF_COST','INTERNAL_ELIMINATION','ENTITY_VAT_LEDGER','CASH_INFLOW','CASH_OUTFLOW','INTERNAL_CASH_ELIMINATION')")
    op.create_check_constraint("ck_group_penetration_components_typed_source", "group_penetration_components", "(component_type IN ('EXTERNAL_REVENUE','EXTERNAL_LEAF_COST','INTERNAL_ELIMINATION') AND fulfillment_fact_id IS NOT NULL AND entity_vat_ledger_id IS NULL AND payment_fact_id IS NULL) OR (component_type='ENTITY_VAT_LEDGER' AND fulfillment_fact_id IS NULL AND entity_vat_ledger_id IS NOT NULL AND payment_fact_id IS NULL) OR (component_type IN ('CASH_INFLOW','CASH_OUTFLOW','INTERNAL_CASH_ELIMINATION') AND fulfillment_fact_id IS NULL AND entity_vat_ledger_id IS NULL AND payment_fact_id IS NOT NULL)")
    op.create_unique_constraint("uq_group_penetration_component_payment", "group_penetration_components", ["result_id", "component_type", "payment_fact_id"])
    op.create_index("ix_group_penetration_components_payment_fact", "group_penetration_components", ["payment_fact_id"])


def downgrade() -> None:
    _require_postgresql()
    op.drop_index("ix_group_penetration_components_payment_fact", table_name="group_penetration_components")
    op.drop_constraint("uq_group_penetration_component_payment", "group_penetration_components", type_="unique")
    op.drop_constraint("ck_group_penetration_components_typed_source", "group_penetration_components", type_="check")
    op.drop_constraint("ck_group_penetration_components_type", "group_penetration_components", type_="check")
    op.create_check_constraint("ck_group_penetration_components_type", "group_penetration_components", "component_type IN ('EXTERNAL_REVENUE','EXTERNAL_LEAF_COST','INTERNAL_ELIMINATION','ENTITY_VAT_LEDGER')")
    op.create_check_constraint("ck_group_penetration_components_typed_source", "group_penetration_components", "(component_type IN ('EXTERNAL_REVENUE','EXTERNAL_LEAF_COST','INTERNAL_ELIMINATION') AND fulfillment_fact_id IS NOT NULL AND entity_vat_ledger_id IS NULL) OR (component_type='ENTITY_VAT_LEDGER' AND fulfillment_fact_id IS NULL AND entity_vat_ledger_id IS NOT NULL)")
    op.drop_column("group_penetration_components", "payment_fact_id")
    op.drop_index("ix_legacy_cashflow_map_status", table_name="legacy_cashflow_map")
    op.drop_index("ix_legacy_cashflow_map_payment_fact", table_name="legacy_cashflow_map")
    op.drop_table("legacy_cashflow_map")
    op.drop_index("ix_payment_facts_bank_reference", table_name="payment_facts")
    op.drop_index("ix_payment_facts_payee_date", table_name="payment_facts")
    op.drop_index("ix_payment_facts_payer_date", table_name="payment_facts")
    op.drop_table("payment_facts")
