"""Validate the shared-master invariants after convergence.
Revision ID: 006_shared_schema_constraints
Revises: 005_pgvector_indexes
"""
from alembic import op
from sqlalchemy import text
revision='006_shared_schema_constraints'; down_revision='005_pgvector_indexes'; branch_labels=None; depends_on=None
def upgrade():
  if op.get_bind().dialect.name!='postgresql': raise RuntimeError('PostgreSQL-only migration')
  # Fail closed if alias triggers are missing; Tax migrations own them.
  for trig in ('trg_sync_project_aliases','trg_sync_entity_aliases'):
    ok=op.get_bind().execute(text("SELECT 1 FROM pg_trigger WHERE tgname=:n AND NOT tgisinternal"),{'n':trig}).first()
    if not ok: raise RuntimeError(f'missing Tax-owned shared alias trigger: {trig}')
def downgrade(): pass
