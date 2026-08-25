"""Manage pgvector and FTS indexes under Alembic.
Revision ID: 005_pgvector_indexes
Revises: 004_postgresql_canonical_analytics
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import inspect, text

from alembic import op

revision = "005_pgvector_indexes"
down_revision = "004_postgresql_canonical_analytics"
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
    """Recognise PostgreSQL's reflected ``vector(n)`` type safely."""
    return bool(re.match(r"^vector(?:\s*\(\s*\d+\s*\))?$", str(column_type).strip().lower()))


def _convert_embedding_column(table_name: str) -> None:
    """Converge a legacy TEXT embedding column to vector(1024).

    Older V1.1 baselines stored the JSON/vector text in ``embedding`` while the
    application ORM already expected pgvector.  A temporary column preserves
    the original value until every non-empty value has been cast successfully;
    malformed non-empty vectors therefore abort the migration instead of being
    silently discarded.  Empty placeholders (NULL, empty string, ``[]`` and
    ``null``) become NULL, while ``embedding_json`` remains the durable JSON
    fallback/audit representation.
    """
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {item["name"]: item for item in inspector.get_columns(table_name)}
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

    # Prefer the canonical JSON payload, but accept a valid legacy value in the
    # old embedding column.  The cast is intentionally strict: invalid data
    # raises and PostgreSQL rolls back the entire revision.
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


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    op.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    for table_name in _EMBEDDING_TABLES:
        _convert_embedding_column(table_name)
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chunks_search_text_fts ON chunks USING gin(to_tsvector('simple', search_text)) WHERE search_text IS NOT NULL AND search_text <> ''"
        )
    )


def downgrade():
    op.execute(text("DROP INDEX IF EXISTS ix_chunks_search_text_fts"))
    op.execute(text("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw"))
