"""Scope deterministic risk replacement to scanner-owned rows.

Revision ID: 67_risk_event_scope
Revises: 66_canonical_eac_function
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "67_risk_event_scope"
down_revision = "66_canonical_eac_function"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk_events",
        sa.Column("source", sa.String(length=64), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "risk_events",
        sa.Column("rule_version", sa.String(length=64), nullable=False, server_default="legacy"),
    )
    op.create_index(
        "ix_risk_events_project_source_rule",
        "risk_events",
        ["project_id", "source", "rule_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_risk_events_project_source_rule", table_name="risk_events")
    op.drop_column("risk_events", "rule_version")
    op.drop_column("risk_events", "source")
