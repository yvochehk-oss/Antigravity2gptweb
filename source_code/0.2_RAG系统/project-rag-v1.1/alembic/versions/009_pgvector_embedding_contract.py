"""Converge every ORM embedding column to pgvector.

Revision 005 handles the original 001 -> 005 path.  Databases that were
already stamped at the previous RAG head (006/008) can skip 005, however, and
historically retained TEXT columns on the regulation tables.  This follow-up
revision is therefore deliberately idempotent and upgrades those existing
databases without touching the Tax-owned facts tables.
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import inspect, text

from alembic import op

revision = "009_pgvector_embedding_contract"
down_revision = "008_shared_facts_snapshot_contract"
branch_labels = None
depends_on = None

_VECTOR_DIM = 1024
_EMBEDDING_TABLES = (
    "chunks",
    "regulations",
    "regulation_articles",
    "regulation_chunks",
)


def _is_vector_type(column_type: object) -> bool:
    return bool(
        re.match(
            r"^vector(?:\s*\(\s*\d+\s*\))?$",
            str(column_type).strip().lower(),
        )
    )


def _convert_embedding_column(table_name: str) -> None:
    bind = op.get_bind()
    columns = {item["name"]: item for item in inspect(bind).get_columns(table_name)}
    if "embedding" not in columns:
        raise RuntimeError(f"{table_name}.embedding is required for the pgvector contract")
    if _is_vector_type(columns["embedding"]["type"]):
        return

    temp_name = "_embedding_vector_v2"
    if temp_name not in columns:
        op.add_column(
            table_name,
            sa.Column(temp_name, Vector(_VECTOR_DIM), nullable=True),
        )

    # Use embedding_json as the canonical source when it contains a vector,
    # and accept the legacy embedding text as a fallback.  Non-empty malformed
    # data is intentionally allowed to abort the transaction; silently
    # replacing it with NULL would destroy retrieval evidence.
    op.execute(
        text(
            f'''UPDATE "{table_name}"
                SET "{temp_name}" = COALESCE(
                    NULLIF(NULLIF(NULLIF(BTRIM("embedding_json"), ''), '[]'), 'null')::vector({_VECTOR_DIM}),
                    NULLIF(NULLIF(NULLIF(BTRIM("embedding"), ''), '[]'), 'null')::vector({_VECTOR_DIM})
                )
                WHERE COALESCE(NULLIF(BTRIM("embedding_json"), ''),
                               NULLIF(BTRIM("embedding"), '')) IS NOT NULL
                  AND COALESCE(NULLIF(BTRIM("embedding_json"), ''),
                               NULLIF(BTRIM("embedding"), '')) NOT IN ('[]', 'null')'''
        )
    )

    op.drop_column(table_name, "embedding")
    op.alter_column(
        table_name,
        temp_name,
        new_column_name="embedding",
        existing_type=Vector(_VECTOR_DIM),
        existing_nullable=True,
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    op.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    for table_name in _EMBEDDING_TABLES:
        _convert_embedding_column(table_name)


def downgrade() -> None:
    # Keep the vector contract on rollback.  Reverting to TEXT would make the
    # running ORM unsafe and would discard the dimension/index guarantee.
    pass
