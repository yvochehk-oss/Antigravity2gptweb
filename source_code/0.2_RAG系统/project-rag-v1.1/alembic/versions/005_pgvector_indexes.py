"""Manage pgvector and FTS indexes under Alembic.
Revision ID: 005_pgvector_indexes
Revises: 004_postgresql_canonical_analytics
"""
from alembic import op
from sqlalchemy import text
revision='005_pgvector_indexes'; down_revision='004_postgresql_canonical_analytics'; branch_labels=None; depends_on=None
def upgrade():
  if op.get_bind().dialect.name!='postgresql': raise RuntimeError('PostgreSQL-only migration')
  op.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
  op.execute(text('CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)'))
  op.execute(text("CREATE INDEX IF NOT EXISTS ix_chunks_search_text_fts ON chunks USING gin(to_tsvector('simple', search_text)) WHERE search_text IS NOT NULL AND search_text <> ''"))
def downgrade():
  op.execute(text('DROP INDEX IF EXISTS ix_chunks_search_text_fts')); op.execute(text('DROP INDEX IF EXISTS ix_chunks_embedding_hnsw'))
