"""Add RAG metadata columns to the Tax-owned shared projects table.
Revision ID: 002_analytics_schema
Revises: 001_initial
"""
from alembic import op
import sqlalchemy as sa
revision='002_analytics_schema'; down_revision='001_initial'; branch_labels=None; depends_on=None
def upgrade():
    if op.get_bind().dialect.name!='postgresql': raise RuntimeError('PostgreSQL-only migration')
    cols={c['name'] for c in sa.inspect(op.get_bind()).get_columns('projects')}
    for name,col in {
      'external_system':sa.Column('external_system',sa.String(80),nullable=True),
      'external_project_id':sa.Column('external_project_id',sa.String(120),nullable=True),
      'entity_code':sa.Column('entity_code',sa.String(16),nullable=True),
      'status':sa.Column('status',sa.String(24),nullable=True),
      'start_date':sa.Column('start_date',sa.String(20),nullable=True),
      'expected_end_date':sa.Column('expected_end_date',sa.String(20),nullable=True),
      'project_type':sa.Column('project_type',sa.String(32),nullable=True),
      'note':sa.Column('note',sa.Text(),nullable=True),
      'created_at':sa.Column('created_at',sa.String(40),nullable=True),
      'updated_at':sa.Column('updated_at',sa.String(40),nullable=True),
    }.items():
      if name not in cols: op.add_column('projects',col)
def downgrade(): raise RuntimeError('shared PostgreSQL convergence is intentionally irreversible')
