"""Replace heuristic Invoice/RealCost dedupe with explicit provenance links.

Revision ID: 68_real_cost_invoice_provenance
Revises: 67_risk_event_scope
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "68_real_cost_invoice_provenance"
down_revision = "67_risk_event_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "real_cost_invoice_links",
        sa.Column(
            "real_cost_id",
            sa.Integer(),
            sa.ForeignKey("real_costs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "invoice_id",
            sa.Integer(),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=64),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("source_record_id", sa.String(length=128), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=True),
        sa.UniqueConstraint(
            "invoice_id",
            name="uq_real_cost_invoice_links_invoice_id",
        ),
    )
    op.create_index(
        "ix_real_cost_invoice_links_invoice_id",
        "real_cost_invoice_links",
        ["invoice_id"],
        unique=False,
    )
    op.create_index(
        "ix_real_cost_invoice_links_source_fingerprint",
        "real_cost_invoice_links",
        ["source_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_real_cost_invoice_links_source_fingerprint",
        table_name="real_cost_invoice_links",
    )
    op.drop_index(
        "ix_real_cost_invoice_links_invoice_id",
        table_name="real_cost_invoice_links",
    )
    op.drop_table("real_cost_invoice_links")
