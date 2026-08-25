"""Add the canonical-entity authenticity gate to Analytics/Facts views.

Projects are allowed to remain visible when their lead ``entity_code`` is
missing, invalid, or absent from the canonical ``entities`` master.  They are
not allowed to become trusted Canonical Facts in that state.  This revision
refreshes the reviewable SQL view definitions in place and does not update
project or entity data.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from alembic import op

revision = "011_entity_mapping_facts_gate"
down_revision = "010_analytics_contract_views"
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
    """Install the authenticity gate without touching Tax-owned fact rows."""

    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("entity mapping Facts gate is PostgreSQL-only")

    # This view is recreated last and has no downstream dependencies.  Drop it
    # first so the migration is safe on both 010 databases and a partially
    # converged database where the metadata view was absent.
    op.execute(text("DROP VIEW IF EXISTS facts_provider_tables"))
    view_root = _view_root()
    for filename in _VIEW_FILES:
        sql_file = view_root / filename
        if not sql_file.is_file():
            raise RuntimeError(f"analytics view SQL file is missing: {sql_file}")
        op.execute(text(sql_file.read_text(encoding="utf-8")))


def downgrade() -> None:
    """Keep the fail-closed view definition when only the marker is rolled back.

    The prior 010 migration has the same non-destructive view-only downgrade
    policy.  Reconstructing the pre-gate view would re-enable untrusted
    aggregate Facts, so the SQL contract intentionally remains fail-closed.
    """

    pass
