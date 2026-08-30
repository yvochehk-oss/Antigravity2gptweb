"""V3 A4: widen external counterparty reference columns to VARCHAR(64).

Revision ID: 72_v3_boundary_hotfix
Revises: 71_timezone_aware_timestamps

Internal canonical ``entity_code`` columns remain VARCHAR(16).  Only fields
that can hold an external buyer/seller/counterparty identifier are widened.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "72_v3_boundary_hotfix"
down_revision = "71_timezone_aware_timestamps"
branch_labels = None
depends_on = None

_TARGETS = (
    ("contracts", "buyer_code"),
    ("contracts", "seller_code"),
    ("invoices", "counterparty_code"),
    ("cashflows", "counterparty_code"),
    ("fulfillment", "counterparty_code"),
    ("real_costs", "counterparty_code"),
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

    # Narrowing after V3 data has been written is destructive.  Fail closed
    # instead of silently truncating an external identifier.
    offenders: list[str] = []
    for table, column in _TARGETS:
        count = bind.execute(
            sa.text(
                f'SELECT COUNT(*) FROM "{table}" '
                f'WHERE LENGTH("{column}") > 16'
            )
        ).scalar_one()
        if count:
            offenders.append(f"{table}.{column}={int(count)}")

    if offenders:
        raise RuntimeError(
            "Refusing V3 A4 downgrade: values longer than 16 characters exist in "
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
