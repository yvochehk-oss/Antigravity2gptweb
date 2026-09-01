"""Add Task 06 Party migration conflict registry.

Revision ID: 76_v3_party_migration_conflicts
Revises: 75_v3_taxpayer_profiles

This migration creates only the review/audit registry. It does not backfill
Party rows and never changes legacy business facts.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "76_v3_party_migration_conflicts"
down_revision = "75_v3_taxpayer_profiles"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.create_table(
        "party_migration_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conflict_key", sa.String(64), nullable=False),
        sa.Column("conflict_type", sa.String(48), nullable=False),
        sa.Column("subject_code", sa.String(64), nullable=True),
        sa.Column("related_code", sa.String(64), nullable=True),
        sa.Column("legacy_source", sa.String(120), nullable=False),
        sa.Column("detector", sa.String(40), nullable=False, server_default="TASK06_BACKFILL"),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("reviewed_by", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN','RESOLVED','IGNORED','CLEARED')",
            name="ck_party_migration_conflicts_status",
        ),
        sa.UniqueConstraint("conflict_key", name="uq_party_migration_conflicts_key"),
    )
    op.create_index(
        "ix_party_migration_conflicts_status",
        "party_migration_conflicts",
        ["status"],
    )
    op.create_index(
        "ix_party_migration_conflicts_type",
        "party_migration_conflicts",
        ["conflict_type"],
    )
    op.create_index(
        "ix_party_migration_conflicts_subject",
        "party_migration_conflicts",
        ["subject_code"],
    )


def downgrade() -> None:
    _require_postgresql()
    op.drop_index(
        "ix_party_migration_conflicts_subject",
        table_name="party_migration_conflicts",
    )
    op.drop_index(
        "ix_party_migration_conflicts_type",
        table_name="party_migration_conflicts",
    )
    op.drop_index(
        "ix_party_migration_conflicts_status",
        table_name="party_migration_conflicts",
    )
    op.drop_table("party_migration_conflicts")
