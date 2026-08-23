"""PostgreSQL shared-master compatibility columns.
Revision ID: 58c1_postgresql_shared_master
Revises: 57eaaaffc6eb
"""
from alembic import op
import sqlalchemy as sa
revision='58c1_postgresql_shared_master'; down_revision='57eaaaffc6eb'; branch_labels=None; depends_on=None

def _add(table,name,col):
    cols={c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}
    if name not in cols: op.add_column(table,col)

def upgrade():
    if op.get_bind().dialect.name!='postgresql': raise RuntimeError('PostgreSQL-only migration')
    op.alter_column('projects','code',type_=sa.String(64),existing_type=sa.String(30))
    op.alter_column('entities','code',type_=sa.String(16),existing_type=sa.String(8))
    op.alter_column('entities','parent_entity_code',type_=sa.String(16),existing_type=sa.String(8))
    op.alter_column('entities','name',type_=sa.String(200),existing_type=sa.String(100))
    op.alter_column('entities','short_name',type_=sa.String(80),existing_type=sa.String(60))
    op.alter_column('entities','business_role',type_=sa.String(32),existing_type=sa.String(1))
    # Fail closed rather than silently coercing/deleting rows that do not belong to
    # the confirmed 26-unit internal master. External counterparties must be
    # migrated to external_parties before convergence.
    canonical = (
      'A01','A02','A03','A04','A05','A06','A07','A08','A09','A10','A11',
      'B01','B02','B03','B04','B05','B06','B07','B08','B09','B10',
      'C01','C02','D01','D02','D03',
    )
    quoted_codes = ",".join(f"'{code}'" for code in canonical)
    rows = op.get_bind().execute(
        sa.text(f"SELECT code FROM entities WHERE code IS NULL OR code NOT IN ({quoted_codes}) ORDER BY code NULLS FIRST")
    ).scalars().all()
    if rows:
        preview = ", ".join("<NULL>" if code is None else str(code) for code in rows[:20])
        suffix = " ..." if len(rows) > 20 else ""
        raise RuntimeError(
            "entities contains rows outside the confirmed 26-unit internal master: "
            f"{preview}{suffix}. Move external counterparties to external_parties before migration."
        )

    # Reassert the confirmed 26-unit closed-set constraint after shared-column convergence.
    op.execute("ALTER TABLE entities DROP CONSTRAINT IF EXISTS ck_entities_canonical_code")
    op.execute("""ALTER TABLE entities ADD CONSTRAINT ck_entities_canonical_code CHECK (code IN (
      'A01','A02','A03','A04','A05','A06','A07','A08','A09','A10','A11',
      'B01','B02','B03','B04','B05','B06','B07','B08','B09','B10',
      'C01','C02','D01','D02','D03'))""")
    _add('projects','project_code',sa.Column('project_code',sa.String(64),nullable=True))
    _add('projects','contract_amount',sa.Column('contract_amount',sa.Numeric(18,2),nullable=True))
    _add('projects','location',sa.Column('location',sa.String(200),nullable=True))
    _add('entities','entity_code',sa.Column('entity_code',sa.String(16),nullable=True))
    _add('entities','entity_kind',sa.Column('entity_kind',sa.String(24),nullable=True))
    _add('entities','status',sa.Column('status',sa.String(24),nullable=True))
    op.execute("UPDATE projects SET project_code=code WHERE project_code IS NULL")
    op.execute("UPDATE projects SET contract_amount=contract_total WHERE contract_amount IS NULL")
    op.execute("UPDATE projects SET location=city WHERE location IS NULL")
    op.execute("UPDATE entities SET entity_code=code WHERE entity_code IS NULL")
    op.execute("UPDATE entities SET entity_kind=COALESCE(NULLIF(kind,''),'company') WHERE entity_kind IS NULL")
    op.execute("UPDATE entities SET status=CASE WHEN active THEN 'active' ELSE 'inactive' END WHERE status IS NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_project_code ON projects(project_code)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_entities_entity_code ON entities(entity_code)")

def downgrade():
    raise RuntimeError('shared PostgreSQL convergence migration is intentionally irreversible')
