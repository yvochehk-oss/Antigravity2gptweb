"""Add Task 07b red/void/correction Fact relationships.

Revision ID: 78_v3_invoice_fact_relationships
Revises: 77_v3_fact_core_invoice

The migration is additive. Red invoices remain independent Invoice Facts and
legacy readers remain on ``invoices``. ``invoice_type`` is retained only for
compatibility while the canonical classification is split into medium/category.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "78_v3_invoice_fact_relationships"
down_revision = "77_v3_fact_core_invoice"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.execute("ALTER TABLE alembic_version_tax ALTER COLUMN version_num TYPE VARCHAR(64)")

    op.add_column("invoice_facts", sa.Column("invoice_medium", sa.String(20), nullable=True))
    op.add_column("invoice_facts", sa.Column("invoice_category", sa.String(20), nullable=True))
    op.create_check_constraint(
        "ck_invoice_facts_invoice_status",
        "invoice_facts",
        "invoice_status IS NULL OR invoice_status IN ('VALID','VOIDED','RED')",
    )
    op.create_check_constraint(
        "ck_invoice_facts_invoice_medium",
        "invoice_facts",
        "invoice_medium IS NULL OR invoice_medium IN ('DIGITAL','PAPER','OTHER')",
    )
    op.create_check_constraint(
        "ck_invoice_facts_invoice_category",
        "invoice_facts",
        "invoice_category IS NULL OR invoice_category IN ('SPECIAL','ORDINARY','OTHER')",
    )
    op.create_check_constraint(
        "ck_invoice_facts_red_amount_sign",
        "invoice_facts",
        "invoice_status <> 'RED' OR ((net_amount IS NULL OR net_amount <= 0) AND (vat_amount IS NULL OR vat_amount <= 0) AND (gross_amount IS NULL OR gross_amount <= 0))",
    )
    op.create_index("ix_invoice_facts_invoice_status", "invoice_facts", ["invoice_status"])

    op.create_table(
        "fact_relationships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_fact_id",
            sa.Integer(),
            sa.ForeignKey("facts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_fact_id",
            sa.Integer(),
            sa.ForeignKey("facts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "relationship_type IN ('REVERSAL_OF','REPLACES','VOID_RELATION','CORRECTS')",
            name="ck_fact_relationships_type",
        ),
        sa.CheckConstraint(
            "source_fact_id <> target_fact_id",
            name="ck_fact_relationships_not_self",
        ),
        sa.UniqueConstraint(
            "source_fact_id",
            "target_fact_id",
            "relationship_type",
            name="uq_fact_relationships_source_target_type",
        ),
    )
    op.create_index("ix_fact_relationships_source", "fact_relationships", ["source_fact_id"])
    op.create_index("ix_fact_relationships_target", "fact_relationships", ["target_fact_id"])
    op.create_index("ix_fact_relationships_type", "fact_relationships", ["relationship_type"])


def downgrade() -> None:
    _require_postgresql()

    op.drop_index("ix_fact_relationships_type", table_name="fact_relationships")
    op.drop_index("ix_fact_relationships_target", table_name="fact_relationships")
    op.drop_index("ix_fact_relationships_source", table_name="fact_relationships")
    op.drop_table("fact_relationships")

    op.drop_index("ix_invoice_facts_invoice_status", table_name="invoice_facts")
    op.drop_constraint("ck_invoice_facts_red_amount_sign", "invoice_facts", type_="check")
    op.drop_constraint("ck_invoice_facts_invoice_category", "invoice_facts", type_="check")
    op.drop_constraint("ck_invoice_facts_invoice_medium", "invoice_facts", type_="check")
    op.drop_constraint("ck_invoice_facts_invoice_status", "invoice_facts", type_="check")
    op.drop_column("invoice_facts", "invoice_category")
    op.drop_column("invoice_facts", "invoice_medium")
