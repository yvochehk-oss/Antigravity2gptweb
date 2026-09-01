"""Add Task18 canonical group penetration result projections.

Revision ID: 89_v3_group_penetration
Revises: 88_v3_project_tax_analysis
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "89_v3_group_penetration"
down_revision = "88_v3_project_tax_analysis"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "group_penetration_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("calculation_run_id", sa.Integer(), sa.ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("anchor_party_id", sa.Integer(), sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("analysis_period", sa.Date(), nullable=False),
        sa.Column("basis", sa.String(12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="READY"),
        sa.Column("external_revenue", sa.Numeric(18, 2), nullable=True),
        sa.Column("external_leaf_cost", sa.Numeric(18, 2), nullable=True),
        sa.Column("internal_eliminated", sa.Numeric(18, 2), nullable=True),
        sa.Column("group_gross_margin", sa.Numeric(18, 2), nullable=True),
        sa.Column("tax_output_vat", sa.Numeric(18, 2), nullable=True),
        sa.Column("tax_input_vat", sa.Numeric(18, 2), nullable=True),
        sa.Column("tax_prepayment", sa.Numeric(18, 2), nullable=True),
        sa.Column("tax_payable_after_prepayment", sa.Numeric(18, 2), nullable=True),
        sa.Column("cash_inflow", sa.Numeric(18, 2), nullable=True),
        sa.Column("cash_outflow", sa.Numeric(18, 2), nullable=True),
        sa.Column("net_cash", sa.Numeric(18, 2), nullable=True),
        sa.Column("cycle_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("EXTRACT(DAY FROM analysis_period) = 1", name="ck_group_penetration_period_month_start"),
        sa.CheckConstraint("basis IN ('ACCRUAL','TAX','CASH')", name="ck_group_penetration_basis"),
        sa.CheckConstraint("status IN ('READY','NOT_READY','CYCLE_DETECTED')", name="ck_group_penetration_status"),
        sa.CheckConstraint("max_depth >= 0", name="ck_group_penetration_depth_nonnegative"),
        sa.CheckConstraint("length(input_snapshot_sha256)=64 AND length(result_sha256)=64", name="ck_group_penetration_hash_lengths"),
        sa.CheckConstraint("""
            (basis='ACCRUAL' AND external_revenue IS NOT NULL AND external_leaf_cost IS NOT NULL
             AND internal_eliminated IS NOT NULL AND group_gross_margin IS NOT NULL
             AND tax_output_vat IS NULL AND tax_input_vat IS NULL AND tax_prepayment IS NULL
             AND tax_payable_after_prepayment IS NULL AND cash_inflow IS NULL AND cash_outflow IS NULL AND net_cash IS NULL)
            OR
            (basis='TAX' AND external_revenue IS NULL AND external_leaf_cost IS NULL
             AND internal_eliminated IS NULL AND group_gross_margin IS NULL
             AND tax_output_vat IS NOT NULL AND tax_input_vat IS NOT NULL AND tax_prepayment IS NOT NULL
             AND tax_payable_after_prepayment IS NOT NULL AND cash_inflow IS NULL AND cash_outflow IS NULL AND net_cash IS NULL)
            OR
            (basis='CASH' AND external_revenue IS NULL AND external_leaf_cost IS NULL
             AND internal_eliminated IS NULL AND group_gross_margin IS NULL
             AND tax_output_vat IS NULL AND tax_input_vat IS NULL AND tax_prepayment IS NULL
             AND tax_payable_after_prepayment IS NULL AND cash_inflow IS NOT NULL AND cash_outflow IS NOT NULL AND net_cash IS NOT NULL)
            """, name="ck_group_penetration_basis_fields"),
        sa.CheckConstraint("basis <> 'ACCRUAL' OR group_gross_margin = external_revenue - external_leaf_cost", name="ck_group_penetration_accrual_formula"),
        sa.CheckConstraint("basis <> 'CASH' OR net_cash = cash_inflow - cash_outflow", name="ck_group_penetration_cash_formula"),
        sa.CheckConstraint("status <> 'READY' OR cycle_detected IS FALSE", name="ck_group_penetration_ready_no_cycle"),
    )
    op.create_index("ix_group_penetration_scope", "group_penetration_results", ["anchor_party_id", "analysis_period", "basis"])

    op.create_table(
        "group_penetration_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("result_id", sa.Integer(), sa.ForeignKey("group_penetration_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("component_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("fulfillment_fact_id", sa.Integer(), sa.ForeignKey("fulfillment_facts.fact_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("entity_vat_ledger_id", sa.Integer(), sa.ForeignKey("entity_vat_ledgers.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("component_type IN ('EXTERNAL_REVENUE','EXTERNAL_LEAF_COST','INTERNAL_ELIMINATION','ENTITY_VAT_LEDGER')", name="ck_group_penetration_components_type"),
        sa.CheckConstraint("depth >= 0", name="ck_group_penetration_components_depth"),
        sa.CheckConstraint("(component_type IN ('EXTERNAL_REVENUE','EXTERNAL_LEAF_COST','INTERNAL_ELIMINATION') AND fulfillment_fact_id IS NOT NULL AND entity_vat_ledger_id IS NULL) OR (component_type='ENTITY_VAT_LEDGER' AND fulfillment_fact_id IS NULL AND entity_vat_ledger_id IS NOT NULL)", name="ck_group_penetration_components_typed_source"),
        sa.UniqueConstraint("result_id", "component_type", "fulfillment_fact_id", name="uq_group_penetration_component_fulfillment"),
        sa.UniqueConstraint("result_id", "entity_vat_ledger_id", name="uq_group_penetration_component_vat_ledger"),
    )
    op.create_index("ix_group_penetration_components_result", "group_penetration_components", ["result_id"])

    op.execute("""
        CREATE FUNCTION v3_guard_group_penetration_scope()
        RETURNS trigger AS $$
        DECLARE r calculation_runs%ROWTYPE;
        DECLARE expected_type text;
        BEGIN
            SELECT * INTO r FROM calculation_runs WHERE id = NEW.calculation_run_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'group penetration calculation run % not found', NEW.calculation_run_id;
            END IF;
            expected_type := CASE NEW.basis
                WHEN 'ACCRUAL' THEN 'GROUP_ACCRUAL'
                WHEN 'TAX' THEN 'GROUP_TAX'
                WHEN 'CASH' THEN 'GROUP_CASH'
            END;
            IF r.run_status <> 'SUCCEEDED' OR r.tax_type <> expected_type
               OR r.reporting_party_id <> NEW.anchor_party_id OR r.tax_period <> NEW.analysis_period THEN
                RAISE EXCEPTION 'group penetration result does not match SUCCEEDED basis run';
            END IF;
            IF r.input_snapshot_sha256 IS DISTINCT FROM NEW.input_snapshot_sha256
               OR r.result_sha256 IS DISTINCT FROM NEW.result_sha256 THEN
                RAISE EXCEPTION 'group penetration hashes do not match calculation run';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_v3_guard_group_penetration_scope
        BEFORE INSERT OR UPDATE OF calculation_run_id, anchor_party_id,
            analysis_period, basis, input_snapshot_sha256, result_sha256
        ON group_penetration_results
        FOR EACH ROW EXECUTE FUNCTION v3_guard_group_penetration_scope()
    """)


def downgrade() -> None:
    _require_postgresql()
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_group_penetration_scope ON group_penetration_results")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_group_penetration_scope()")
    op.drop_index("ix_group_penetration_components_result", table_name="group_penetration_components")
    op.drop_table("group_penetration_components")
    op.drop_index("ix_group_penetration_scope", table_name="group_penetration_results")
    op.drop_table("group_penetration_results")
