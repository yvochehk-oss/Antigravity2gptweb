"""26-unit canonical boundary and project allocation planning tables.
Revision ID: 60a1_project_allocation_planning
Revises: 59d2_shared_alias_triggers
"""
from alembic import op
import sqlalchemy as sa
revision='60a1_project_allocation_planning'; down_revision='59d2_shared_alias_triggers'; branch_labels=None; depends_on=None
CANONICAL=(
 'A01','A02','A03','A04','A05','A06','A07','A08','A09','A10','A11',
 'B01','B02','B03','B04','B05','B06','B07','B08','B09','B10',
 'C01','C02','D01','D02','D03',
)
def upgrade():
    bind=op.get_bind()
    if bind.dialect.name!='postgresql': raise RuntimeError('PostgreSQL-only migration')
    codes=','.join(f"'{x}'" for x in CANONICAL)
    rows=bind.execute(sa.text(f"SELECT code FROM entities WHERE code IS NULL OR code NOT IN ({codes}) ORDER BY code NULLS FIRST")).scalars().all()
    if rows:
        preview=', '.join('<NULL>' if x is None else str(x) for x in rows[:20])
        raise RuntimeError('entities contains rows outside the confirmed 26 system-internal units: '+preview+'. Move genuine external counterparties to external_parties; review/remove synthetic rows before retrying. No row is deleted automatically.')
    # Participant/counterparty codes must fit the shared external-party namespace (EXT-*).
    # The historical VARCHAR(8) columns could not store codes such as EXT-OWNER-01.
    for table, column in (
        ('contracts','buyer_code'),('contracts','seller_code'),
        ('invoices','entity_code'),('invoices','counterparty_code'),
        ('cashflows','entity_code'),('cashflows','counterparty_code'),
        ('fulfillment','counterparty_code'),
        ('real_costs','entity_code'),('real_costs','counterparty_code'),
        ('tax_ledgers','entity_code'),('entity_bank_accounts','entity_code'),
        ('tax_payment_records','entity_code'),
    ):
        op.alter_column(table,column,type_=sa.String(16),existing_type=sa.String(8))

    op.execute('ALTER TABLE entities DROP CONSTRAINT IF EXISTS ck_entities_canonical_code')
    op.execute("ALTER TABLE entities ADD CONSTRAINT ck_entities_canonical_code CHECK (code IN ("+codes+"))")
    op.create_table('planning_scenarios',
      sa.Column('id',sa.Integer(),primary_key=True), sa.Column('project_id',sa.Integer(),sa.ForeignKey('projects.id'),nullable=False),
      sa.Column('package_name',sa.String(120),nullable=False,server_default=''), sa.Column('category',sa.String(30),nullable=False),
      sa.Column('package_amount',sa.Numeric(18,2),nullable=False), sa.Column('objective',sa.String(20),nullable=False,server_default='balanced'),
      sa.Column('status',sa.String(20),nullable=False,server_default='draft'), sa.Column('score',sa.Numeric(8,2),nullable=False,server_default='0'),
      sa.Column('internal_amount',sa.Numeric(18,2),nullable=False,server_default='0'), sa.Column('external_amount',sa.Numeric(18,2),nullable=False,server_default='0'),
      sa.Column('projected_external_cost',sa.Numeric(18,2),nullable=False,server_default='0'), sa.Column('projected_tax_cash',sa.Numeric(18,2),nullable=False,server_default='0'),
      sa.Column('projected_profit',sa.Numeric(18,2),nullable=False,server_default='0'), sa.Column('risk_score',sa.Numeric(6,4),nullable=False,server_default='0'),
      sa.Column('evidence_quality',sa.Numeric(6,4),nullable=False,server_default='0'), sa.Column('request_json',sa.Text(),nullable=False,server_default='{}'),
      sa.Column('result_json',sa.Text(),nullable=False,server_default='{}'), sa.Column('ai_summary',sa.Text(),nullable=False,server_default=''),
      sa.Column('ai_json',sa.Text(),nullable=False,server_default='{}'), sa.Column('created_by',sa.String(80),nullable=False,server_default='system'),
      sa.Column('created_at',sa.String(40),nullable=False,server_default=''))
    op.create_index('ix_planning_scenarios_project_id','planning_scenarios',['project_id'])
    op.create_index('ix_planning_scenarios_category','planning_scenarios',['category'])
    op.create_index('ix_planning_scenarios_status','planning_scenarios',['status'])
    op.create_table('planning_allocations',
      sa.Column('id',sa.Integer(),primary_key=True), sa.Column('scenario_id',sa.Integer(),sa.ForeignKey('planning_scenarios.id'),nullable=False),
      sa.Column('party_scope',sa.String(10),nullable=False), sa.Column('party_code',sa.String(16),nullable=False),
      sa.Column('amount',sa.Numeric(18,2),nullable=False), sa.Column('share',sa.Numeric(8,6),nullable=False),
      sa.Column('estimated_external_cost',sa.Numeric(18,2),nullable=False,server_default='0'), sa.Column('estimated_tax_cash',sa.Numeric(18,2),nullable=False,server_default='0'),
      sa.Column('risk_score',sa.Numeric(6,4),nullable=False,server_default='0'), sa.Column('evidence_quality',sa.Numeric(6,4),nullable=False,server_default='0'),
      sa.Column('rationale',sa.Text(),nullable=False,server_default=''), sa.CheckConstraint("party_scope IN ('internal','external')",name='ck_planning_allocation_scope'))
    op.create_index('ix_planning_allocations_scenario_id','planning_allocations',['scenario_id'])
    op.create_index('ix_planning_allocations_party','planning_allocations',['party_scope','party_code'])
def downgrade():
    op.drop_table('planning_allocations'); op.drop_table('planning_scenarios')
    raise RuntimeError('downgrade cannot safely restore the previously incorrect 44-unit canonical boundary')
