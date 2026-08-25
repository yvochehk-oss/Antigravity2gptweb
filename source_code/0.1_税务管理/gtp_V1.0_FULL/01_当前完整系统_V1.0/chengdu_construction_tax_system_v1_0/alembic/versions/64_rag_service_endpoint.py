"""Persist the administrator-approved Tax -> RAG endpoint.

The endpoint row contains only URL metadata and the DNS address snapshot used
by the SSRF/rebinding guard.  The Tax/RAG shared key remains process supplied
through ``RAG_SHARED_API_KEY`` and is never stored in this table.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "64_rag_service_endpoint"
down_revision = "63_ai_endpoint_routing_pool"
branch_labels = None
depends_on = None

_TABLE = "rag_service_endpoints"
_REQUIRED_COLUMNS = {
    "id",
    "base_url",
    "host",
    "scheme",
    "port",
    "approved_private",
    "resolved_addresses_json",
    "enabled",
    "approved_at",
    "approved_by",
    "last_tested_at",
    "created_at",
    "updated_at",
}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("RAG service endpoint migration is PostgreSQL-only")

    tables = _tables()
    if _TABLE in tables:
        columns = {
            column["name"]
            for column in sa.inspect(bind).get_columns(_TABLE)
        }
        missing = sorted(_REQUIRED_COLUMNS - columns)
        if missing:
            raise RuntimeError(
                f"existing {_TABLE} is incompatible; missing columns: {missing}"
            )
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_url", sa.String(300), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("scheme", sa.String(8), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column(
            "approved_private",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "resolved_addresses_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("approved_at", sa.String(40), nullable=False, server_default=sa.text("''")),
        sa.Column("approved_by", sa.String(80), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "last_tested_at",
            sa.String(40),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("created_at", sa.String(40), nullable=False, server_default=sa.text("''")),
        sa.Column("updated_at", sa.String(40), nullable=False, server_default=sa.text("''")),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_rag_service_endpoints_singleton"),
        sa.CheckConstraint(
            "scheme IN ('http', 'https')",
            name="ck_rag_service_endpoints_scheme",
        ),
        sa.CheckConstraint(
            "port IS NULL OR (port >= 1 AND port <= 65535)",
            name="ck_rag_service_endpoints_port",
        ),
    )
    op.create_index(
        "ix_rag_service_endpoints_host",
        _TABLE,
        ["host"],
    )
    op.create_index(
        "ix_rag_service_endpoints_enabled",
        _TABLE,
        ["enabled"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("RAG service endpoint migration is PostgreSQL-only")
    if _TABLE not in _tables():
        return
    if bind.execute(sa.text(f"SELECT 1 FROM {_TABLE} LIMIT 1")).first() is not None:
        raise RuntimeError(
            "refusing to drop administrator-approved RAG endpoint data; "
            "disable/remove the row explicitly before downgrade"
        )
    op.drop_index("ix_rag_service_endpoints_enabled", table_name=_TABLE)
    op.drop_index("ix_rag_service_endpoints_host", table_name=_TABLE)
    op.drop_table(_TABLE)
