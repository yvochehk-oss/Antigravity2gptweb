"""Add credential references and deterministic endpoint routing metadata.

The existing ``api_key_env`` column is intentionally retained for legacy
deployments.  ``credential_ref`` is only an opaque handle owned by the
application credential store; this migration never stores or derives a secret.
Existing endpoint rows receive the neutral routing defaults and remain enabled
without changing business data.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "63_ai_endpoint_routing_pool"
down_revision = "62_ai_review_batch_status"
branch_labels = None
depends_on = None

_TABLE = "ai_model_endpoints"
_CREDENTIAL_REF = "credential_ref"
_PRIORITY = "priority"
_ROUTING_GROUP = "routing_group"
_CREDENTIAL_UNIQUE = "uq_ai_model_endpoints_credential_ref"
_PRIORITY_CHECK = "ck_ai_model_endpoints_priority_nonnegative"
_ROUTING_CHECK = "ck_ai_model_endpoints_routing_group_format"


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def _check_constraints() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        constraint["name"]
        for constraint in inspector.get_check_constraints(_TABLE)
        if constraint.get("name")
    }


def _unique_constraints() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(_TABLE)
        if constraint.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("AI endpoint routing migration is PostgreSQL-only")
    if _TABLE not in sa.inspect(bind).get_table_names():
        raise RuntimeError(f"{_TABLE} must be created by Tax 001_initial first")

    columns = _columns()
    if _CREDENTIAL_REF not in columns:
        op.add_column(
            _TABLE,
            sa.Column(_CREDENTIAL_REF, sa.String(64), nullable=True),
        )
    if _PRIORITY not in columns:
        # A server default makes this safe for existing rows and keeps direct
        # SQL inserts compatible with the ORM's default.
        op.add_column(
            _TABLE,
            sa.Column(
                _PRIORITY,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("100"),
            ),
        )
    if _ROUTING_GROUP not in columns:
        op.add_column(
            _TABLE,
            sa.Column(
                _ROUTING_GROUP,
                sa.String(40),
                nullable=False,
                server_default=sa.text("'default'"),
            ),
        )

    unique_constraints = _unique_constraints()
    if _CREDENTIAL_UNIQUE not in unique_constraints:
        op.create_unique_constraint(
            _CREDENTIAL_UNIQUE,
            _TABLE,
            [_CREDENTIAL_REF],
        )

    checks = _check_constraints()
    if _PRIORITY_CHECK not in checks:
        op.create_check_constraint(
            _PRIORITY_CHECK,
            _TABLE,
            "priority >= 0",
        )
    if _ROUTING_CHECK not in checks:
        op.create_check_constraint(
            _ROUTING_CHECK,
            _TABLE,
            "routing_group ~ '^[a-z0-9][a-z0-9_/-]{0,39}$'",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("AI endpoint routing migration is PostgreSQL-only")
    if _TABLE not in sa.inspect(bind).get_table_names():
        return

    constraints = _check_constraints()
    if _ROUTING_CHECK in constraints:
        op.drop_constraint(_ROUTING_CHECK, _TABLE, type_="check")
    if _PRIORITY_CHECK in constraints:
        op.drop_constraint(_PRIORITY_CHECK, _TABLE, type_="check")
    if _CREDENTIAL_UNIQUE in _unique_constraints():
        op.drop_constraint(_CREDENTIAL_UNIQUE, _TABLE, type_="unique")

    columns = _columns()
    # These columns were introduced by this revision.  Dropping them is the
    # explicit downgrade contract; the normal upgrade path never removes old
    # ``api_key_env`` or changes existing endpoint rows.
    if _ROUTING_GROUP in columns:
        op.drop_column(_TABLE, _ROUTING_GROUP)
    if _PRIORITY in columns:
        op.drop_column(_TABLE, _PRIORITY)
    if _CREDENTIAL_REF in columns:
        op.drop_column(_TABLE, _CREDENTIAL_REF)
