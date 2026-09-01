"""Add Task 08 bridge from legacy real-cost provenance to InvoiceFact.

Revision ID: 79_v3_legacy_invoice_pilot_bridge
Revises: 78_v3_invoice_fact_relationships

This migration is additive only.  The legacy ``invoice_id`` link remains the
source-compatible reference while the nullable ``invoice_fact_id`` bridge lets
pilot-migrated invoices preserve the same real-cost evidence chain.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "79_v3_legacy_invoice_pilot_bridge"
down_revision = "78_v3_invoice_fact_relationships"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.add_column(
        "real_cost_invoice_links",
        sa.Column(
            "invoice_fact_id",
            sa.Integer(),
            sa.ForeignKey("invoice_facts.fact_id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    # Deliberately non-unique: two legacy perspective rows may deterministically
    # collapse into one physical InvoiceFact while each legacy provenance row is
    # retained for auditability.
    op.create_index(
        "ix_real_cost_invoice_links_invoice_fact_id",
        "real_cost_invoice_links",
        ["invoice_fact_id"],
        unique=False,
    )


def downgrade() -> None:
    _require_postgresql()
    bind = op.get_bind()
    populated = bind.execute(
        sa.text(
            "SELECT count(*) FROM real_cost_invoice_links "
            "WHERE invoice_fact_id IS NOT NULL"
        )
    ).scalar_one()
    if int(populated):
        raise RuntimeError(
            "refusing downgrade: real_cost_invoice_links.invoice_fact_id contains "
            "Task 08 provenance data; clear it explicitly before downgrade"
        )
    op.drop_index(
        "ix_real_cost_invoice_links_invoice_fact_id",
        table_name="real_cost_invoice_links",
    )
    op.drop_column("real_cost_invoice_links", "invoice_fact_id")
