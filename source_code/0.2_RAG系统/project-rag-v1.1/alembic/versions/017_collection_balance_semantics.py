"""Separate unpaid receivables from advance/excess collections.

Revision ID: 017_collection_balance_semantics
Revises: 016_canonical_eac_contract
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from alembic import op

revision = "017_collection_balance_semantics"
down_revision = "016_canonical_eac_contract"
branch_labels = None
depends_on = None

ROOT = Path(__file__).resolve().parents[2] / "sql" / "views"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    op.execute(text("DROP VIEW IF EXISTS analytics_project_full"))
    op.execute(text("DROP VIEW IF EXISTS analytics_eac"))
    op.execute(text("DROP VIEW IF EXISTS analytics_project_profit"))
    for filename in (
        "analytics_project_profit.sql",
        "analytics_eac.sql",
        "analytics_project_full.sql",
    ):
        op.execute(text((ROOT / filename).read_text(encoding="utf-8")))


def downgrade() -> None:
    pass
