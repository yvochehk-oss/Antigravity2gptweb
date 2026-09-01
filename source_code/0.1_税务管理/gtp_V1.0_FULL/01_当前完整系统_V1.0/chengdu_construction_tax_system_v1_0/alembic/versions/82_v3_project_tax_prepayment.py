"""Add Project Tax Treatment rules and Tax Prepayment Fact subtype.

Revision ID: 82_v3_project_tax_prepayment
Revises: 81_v3_contract_fulfillment_facts

Task12 is additive. It creates TAX-axis structures only; no CASH/ACCRUAL reader
or legacy project row is rewritten. Prepayment rates are deliberately not
stored or hard-coded by this migration.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "82_v3_project_tax_prepayment"
down_revision = "81_v3_contract_fulfillment_facts"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "project_tax_treatments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tax_type", sa.String(32), nullable=False),
        sa.Column("treatment_code", sa.String(64), nullable=False),
        sa.Column(
            "reporting_party_id",
            sa.Integer(),
            sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("source", sa.String(240), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_project_tax_treatments_effective_range",
        ),
    )
    op.create_index(
        "ix_project_tax_treatments_project_tax_from",
        "project_tax_treatments",
        ["project_id", "tax_type", "effective_from"],
    )
    op.create_index(
        "ix_project_tax_treatments_reporting_party",
        "project_tax_treatments",
        ["reporting_party_id"],
    )
    op.execute(
        """
        ALTER TABLE project_tax_treatments
        ADD CONSTRAINT ex_project_tax_treatments_no_overlap
        EXCLUDE USING gist (
            project_id WITH =,
            tax_type WITH =,
            daterange(effective_from, effective_to, '[]') WITH &&
        )
        """
    )

    op.create_table(
        "tax_prepayment_facts",
        sa.Column(
            "fact_id",
            sa.Integer(),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reporting_party_id",
            sa.Integer(),
            sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tax_type", sa.String(32), nullable=False),
        sa.Column("tax_period", sa.Date(), nullable=False),
        sa.Column("tax_event_date", sa.Date(), nullable=False),
        sa.Column("taxable_base", sa.Numeric(18, 2), nullable=True),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False, server_default="PREPAYMENT"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column(
            "treatment_id",
            sa.Integer(),
            sa.ForeignKey("project_tax_treatments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source_system", sa.String(40), nullable=True),
        sa.Column("external_reference", sa.String(160), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_tax_prepayment_facts_period_month_start",
        ),
        sa.CheckConstraint(
            "taxable_base IS NULL OR taxable_base >= 0",
            name="ck_tax_prepayment_facts_taxable_base_nonnegative",
        ),
        sa.CheckConstraint(
            "event_type IN ('PREPAYMENT','REVERSAL','ADJUSTMENT')",
            name="ck_tax_prepayment_facts_event_type",
        ),
        sa.CheckConstraint(
            "(event_type <> 'PREPAYMENT' OR tax_amount > 0) AND "
            "(event_type <> 'REVERSAL' OR tax_amount < 0) AND "
            "(event_type <> 'ADJUSTMENT' OR tax_amount <> 0)",
            name="ck_tax_prepayment_facts_event_sign",
        ),
        sa.UniqueConstraint(
            "source_system",
            "external_reference",
            name="uq_tax_prepayment_facts_source_reference",
        ),
    )
    op.create_index(
        "ix_tax_prepayment_facts_project_period",
        "tax_prepayment_facts",
        ["project_id", "tax_type", "tax_period"],
    )
    op.create_index(
        "ix_tax_prepayment_facts_reporting_period",
        "tax_prepayment_facts",
        ["reporting_party_id", "tax_type", "tax_period"],
    )
    op.create_index(
        "ix_tax_prepayment_facts_treatment_id",
        "tax_prepayment_facts",
        ["treatment_id"],
    )


def downgrade() -> None:
    _require_postgresql()
    op.drop_index("ix_tax_prepayment_facts_treatment_id", table_name="tax_prepayment_facts")
    op.drop_index("ix_tax_prepayment_facts_reporting_period", table_name="tax_prepayment_facts")
    op.drop_index("ix_tax_prepayment_facts_project_period", table_name="tax_prepayment_facts")
    op.drop_table("tax_prepayment_facts")

    op.execute(
        "ALTER TABLE project_tax_treatments "
        "DROP CONSTRAINT IF EXISTS ex_project_tax_treatments_no_overlap"
    )
    op.drop_index(
        "ix_project_tax_treatments_reporting_party",
        table_name="project_tax_treatments",
    )
    op.drop_index(
        "ix_project_tax_treatments_project_tax_from",
        table_name="project_tax_treatments",
    )
    op.drop_table("project_tax_treatments")
    # btree_gist is shared infrastructure; never drop it here.
