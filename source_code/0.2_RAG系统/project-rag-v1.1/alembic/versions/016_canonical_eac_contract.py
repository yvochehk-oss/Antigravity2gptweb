"""Refresh analytics EAC onto the shared canonical PostgreSQL function.

Revision ID: 016_canonical_eac_contract
Revises: 015_rag_llm_model_pool

The supported deployment migrates Tax before ProjectRAG. Tax revision
66_canonical_eac_function owns the shared function; this migration fails with
a clear message rather than silently installing a second formula.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from alembic import op

revision = "016_canonical_eac_contract"
down_revision = "015_rag_llm_model_pool"
branch_labels = None
depends_on = None

ROOT = Path(__file__).resolve().parents[2]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    function_exists = bind.scalar(
        text(
            "SELECT to_regprocedure("
            "'canonical_management_eac_cost(numeric,numeric,numeric)'"
            ") IS NOT NULL"
        )
    )
    if not function_exists:
        raise RuntimeError(
            "canonical_management_eac_cost is missing; run the Tax migration "
            "chain through 66_canonical_eac_function before ProjectRAG migrations"
        )
    sql = (ROOT / "sql" / "views" / "analytics_eac.sql").read_text(encoding="utf-8")
    op.execute(sql)


def downgrade() -> None:
    pass
