"""Complete V3 S0-03 by widening local legacy entity references.

Revision ID: 73_v3_boundary_entity_refs
Revises: 72_v3_boundary_hotfix

Revision 72 was already applied to deployed databases before Database Core
v1.2 expanded the boundary contract from six external references to all nine
legacy party-code references.  Never rewrite an applied revision: this
additive follow-up widens only the three columns absent from revision 72.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "73_v3_boundary_entity_refs"
down_revision = "72_v3_boundary_hotfix"
branch_labels = None
depends_on = None

_TARGETS = (
    ("invoices", "entity_code"),
    ("cashflows", "entity_code"),
    ("real_costs", "entity_code"),
)


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    for table, column in _TARGETS:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(16),
            type_=sa.String(64),
            existing_nullable=False,
        )


def downgrade() -> None:
    _require_postgresql()
    bind = op.get_bind()
    offenders: list[str] = []
    for table, column in _TARGETS:
        count = bind.execute(
            sa.text(f'SELECT COUNT(*) FROM "{table}" WHERE LENGTH("{column}") > 16')
        ).scalar_one()
        if count:
            offenders.append(f"{table}.{column}={int(count)}")
    if offenders:
        raise RuntimeError(
            "Refusing V3 entity-reference downgrade: values longer than 16 characters exist in "
            + ", ".join(offenders)
        )
    for table, column in reversed(_TARGETS):
        op.alter_column(
            table,
            column,
            existing_type=sa.String(64),
            type_=sa.String(16),
            existing_nullable=False,
        )
