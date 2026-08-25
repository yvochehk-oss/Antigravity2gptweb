"""Widen AI review batch status for terminal diagnostic states."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "62_ai_review_batch_status"
down_revision = "61f_facts_snapshots_canonical"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("AI review batch status migration is PostgreSQL-only")
    exists = bind.execute(
        sa.text("SELECT to_regclass('public.ai_review_batches')")
    ).scalar_one_or_none()
    if exists:
        op.alter_column(
            "ai_review_batches",
            "status",
            type_=sa.String(40),
            existing_type=sa.String(20),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("AI review batch status migration is PostgreSQL-only")
    exists = bind.execute(
        sa.text("SELECT to_regclass('public.ai_review_batches')")
    ).scalar_one_or_none()
    if not exists:
        return
    too_long = bind.execute(
        sa.text(
            "SELECT 1 FROM ai_review_batches "
            "WHERE length(status) > 20 LIMIT 1"
        )
    ).first()
    if too_long:
        raise RuntimeError(
            "refusing to narrow ai_review_batches.status while values exceed 20 characters"
        )
    op.alter_column(
        "ai_review_batches",
        "status",
        type_=sa.String(20),
        existing_type=sa.String(40),
        existing_nullable=False,
    )
