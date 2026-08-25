"""Refresh the canonical analytics view contract for existing RAG 006+ databases.

Revision 004 contains the same definitions for clean installs.  A large part
of the deployed V2 baseline is already stamped at 006, however, so changing
004 alone would leave those databases serving the old view definitions.  This
revision reapplies the reviewable SQL files in dependency order without
touching Tax-owned fact data or replacing any view columns.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from alembic import op

revision = "010_analytics_contract_views"
down_revision = "009_pgvector_embedding_contract"
branch_labels = None
depends_on = None

_VIEW_FILES = (
    "analytics_project_summary.sql",
    "analytics_project_profit.sql",
    "analytics_cost.sql",
    "analytics_eac.sql",
    "analytics_cashflow.sql",
    "analytics_tax.sql",
    "analytics_project_full.sql",
    "facts_provider_tables.sql",
)


def _view_root() -> Path:
    return Path(__file__).resolve().parents[2] / "sql" / "views"


def upgrade() -> None:
    """Install the current analytics SQL contract on an existing database."""

    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("analytics contract views are PostgreSQL-only")
    # The metadata view did not exist in the 006 baseline.  Dropping only this
    # optional object makes its CREATE OR REPLACE statement work on both old
    # and already-converged databases; all analytics views retain their rows
    # and column shape in place.
    op.execute(text("DROP VIEW IF EXISTS facts_provider_tables"))
    view_root = _view_root()
    for filename in _VIEW_FILES:
        sql_file = view_root / filename
        if not sql_file.is_file():
            raise RuntimeError(f"analytics view SQL file is missing: {sql_file}")
        op.execute(text(sql_file.read_text(encoding="utf-8")))


def downgrade() -> None:
    """Keep the compatible view contract when rolling back migration metadata.

    This revision changes view definitions only and does not delete data.  A
    downgrade cannot safely reconstruct every pre-V2 view definition without
    reintroducing the stale Facts contract, so the definitions remain in place
    while the Alembic marker is moved back.
    """

    pass
