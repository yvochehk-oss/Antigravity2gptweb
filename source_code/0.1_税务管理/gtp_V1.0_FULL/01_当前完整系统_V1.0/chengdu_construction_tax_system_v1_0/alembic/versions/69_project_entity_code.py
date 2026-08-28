"""Add the nullable project legal-entity linkage expected by the ORM.

Revision ID: 69_project_entity_code
Revises: 68_real_cost_invoice_provenance

The unified codebase added ``Project.entity_code`` after the previous Tax
migration head. Existing PostgreSQL databases therefore need an explicit,
data-preserving migration; the column stays nullable because historical
projects may not yet have a confirmed legal-entity mapping.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "69_project_entity_code"
down_revision = "68_real_cost_invoice_provenance"
branch_labels = None
depends_on = None

_TABLE = "projects"
_COLUMN = "entity_code"
_INDEX = "ix_projects_entity_code"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if _COLUMN not in columns:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=16), nullable=True))

    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes(_TABLE)}
    if _INDEX not in indexes:
        op.create_index(_INDEX, _TABLE, [_COLUMN], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes(_TABLE)}
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name=_TABLE)

    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if _COLUMN in columns:
        op.drop_column(_TABLE, _COLUMN)
