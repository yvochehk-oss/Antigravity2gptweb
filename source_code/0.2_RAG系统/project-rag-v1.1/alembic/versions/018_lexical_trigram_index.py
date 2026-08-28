"""Add persistent trigram lexical index for Chinese/English retrieval.

Revision ID: 018_lexical_trigram_index
Revises: 017_collection_balance_semantics
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "018_lexical_trigram_index"
down_revision = "017_collection_balance_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    op.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_chunks_search_text_trgm "
        "ON chunks USING gin (search_text gin_trgm_ops) "
        "WHERE search_text IS NOT NULL"
    ))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(text("DROP INDEX IF EXISTS ix_chunks_search_text_trgm"))
