"""Canonical PostgreSQL analytics over Tax-owned truth tables.
Revision ID: 004_postgresql_canonical_analytics
Revises: 003_analytics_project_full
"""

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision = "004_postgresql_canonical_analytics"
down_revision = "003_analytics_project_full"
branch_labels = None
depends_on = None


def _add(table, name, col):
    if name not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}:
        op.add_column(table, col)


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    # RAG descriptive columns on the Tax-owned shared project row.
    project_fields = {
        "external_system": sa.Column("external_system", sa.String(80), nullable=True),
        "external_project_id": sa.Column("external_project_id", sa.String(120), nullable=True),
        "entity_code": sa.Column("entity_code", sa.String(16), nullable=True),
        "status": sa.Column("status", sa.String(24), nullable=True),
        "start_date": sa.Column("start_date", sa.String(20), nullable=True),
        "expected_end_date": sa.Column("expected_end_date", sa.String(20), nullable=True),
        "project_type": sa.Column("project_type", sa.String(32), nullable=True),
        "note": sa.Column("note", sa.Text(), nullable=True),
        "created_at": sa.Column("created_at", sa.String(40), nullable=True),
        "updated_at": sa.Column("updated_at", sa.String(40), nullable=True),
    }
    for n, c in project_fields.items():
        _add("projects", n, c)
    op.execute("UPDATE projects SET status='ACTIVE' WHERE status IS NULL OR status=''")
    op.alter_column(
        "projects", "status", existing_type=sa.String(24), nullable=False, server_default=sa.text("'ACTIVE'")
    )
    # RAG descriptive columns on the shared internal-entity row.
    fields = {
        "entity_type": sa.Column("entity_type", sa.String(32), nullable=True),
        "industry": sa.Column("industry", sa.String(32), nullable=True),
        "legal_representative": sa.Column("legal_representative", sa.String(80), nullable=True),
        "legal_rep_id": sa.Column("legal_rep_id", sa.String(32), nullable=True),
        "legal_rep_phone": sa.Column("legal_rep_phone", sa.String(32), nullable=True),
        "shareholders": sa.Column("shareholders", sa.Text(), nullable=True),
        "supervisor": sa.Column("supervisor", sa.String(80), nullable=True),
        "finance_officer": sa.Column("finance_officer", sa.String(80), nullable=True),
        "registered_capital": sa.Column("registered_capital", sa.String(40), nullable=True),
        "establishment_date": sa.Column("establishment_date", sa.String(20), nullable=True),
        "acquisition_date": sa.Column("acquisition_date", sa.String(20), nullable=True),
        "registration_authority": sa.Column("registration_authority", sa.String(120), nullable=True),
        "registration_number": sa.Column("registration_number", sa.String(40), nullable=True),
        "unified_social_credit_code": sa.Column("unified_social_credit_code", sa.String(40), nullable=True),
        "business_scope": sa.Column("business_scope", sa.Text(), nullable=True),
        "contributed_legal": sa.Column("contributed_legal", sa.Numeric(18, 2), nullable=True),
        "contributed_shareholder": sa.Column("contributed_shareholder", sa.Numeric(18, 2), nullable=True),
        "note": sa.Column("note", sa.Text(), nullable=True),
        "source": sa.Column("source", sa.String(32), nullable=True),
        "data_as_of": sa.Column("data_as_of", sa.String(20), nullable=True),
        "created_at": sa.Column("created_at", sa.String(40), nullable=True),
        "updated_at": sa.Column("updated_at", sa.String(40), nullable=True),
    }
    for n, c in fields.items():
        _add("entities", n, c)
    # Tax may create an internal entity without RAG descriptive metadata. Keep
    # those optional profile fields stable rather than returning NULL surprises.
    string_profile_fields = [
        "entity_type",
        "industry",
        "legal_representative",
        "legal_rep_id",
        "legal_rep_phone",
        "shareholders",
        "supervisor",
        "finance_officer",
        "registered_capital",
        "establishment_date",
        "acquisition_date",
        "registration_authority",
        "registration_number",
        "unified_social_credit_code",
        "business_scope",
        "note",
        "source",
        "data_as_of",
        "created_at",
        "updated_at",
    ]
    for name in string_profile_fields:
        op.execute(text(f"UPDATE entities SET {name}='' WHERE {name} IS NULL"))
        op.execute(text(f"ALTER TABLE entities ALTER COLUMN {name} SET DEFAULT ''"))
    for name in ("contributed_legal", "contributed_shareholder"):
        op.execute(text(f"UPDATE entities SET {name}=0 WHERE {name} IS NULL"))
        op.execute(text(f"ALTER TABLE entities ALTER COLUMN {name} SET DEFAULT 0"))

    # Alias columns are guaranteed by Tax BEFORE INSERT/UPDATE triggers, so
    # they can now become non-null without blocking Tax-originated writes.
    op.execute("UPDATE projects SET project_code=code WHERE project_code IS NULL")
    op.execute("UPDATE projects SET contract_amount=contract_total WHERE contract_amount IS NULL")
    op.execute("UPDATE projects SET location=city WHERE location IS NULL")
    op.execute("UPDATE entities SET entity_code=code WHERE entity_code IS NULL")
    op.execute(
        "UPDATE entities SET entity_kind=COALESCE(NULLIF(entity_kind,''),NULLIF(kind,''),'company') WHERE entity_kind IS NULL OR entity_kind=''"
    )
    op.execute(
        "UPDATE entities SET status=CASE WHEN active THEN 'active' ELSE 'inactive' END WHERE status IS NULL OR status=''"
    )
    op.alter_column("projects", "project_code", existing_type=sa.String(64), nullable=False)
    op.alter_column("projects", "contract_amount", existing_type=sa.Numeric(18, 2), nullable=False)
    op.alter_column("projects", "location", existing_type=sa.String(200), nullable=False)
    op.alter_column("entities", "entity_code", existing_type=sa.String(16), nullable=False)
    op.alter_column("entities", "entity_kind", existing_type=sa.String(24), nullable=False)
    op.alter_column("entities", "status", existing_type=sa.String(24), nullable=False)

    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_projects_entity_code ON projects(entity_code)",
        "CREATE INDEX IF NOT EXISTS ix_projects_status ON projects(status)",
        "CREATE INDEX IF NOT EXISTS ix_entities_code_status ON entities(entity_code,status)",
        "CREATE INDEX IF NOT EXISTS ix_entities_entity_type ON entities(entity_type)",
        "CREATE INDEX IF NOT EXISTS ix_entities_entity_kind ON entities(entity_kind)",
        "CREATE INDEX IF NOT EXISTS ix_entities_status ON entities(status)",
        "CREATE INDEX IF NOT EXISTS ix_entities_industry ON entities(industry)",
        "CREATE INDEX IF NOT EXISTS ix_entities_legal_entity ON entities(legal_entity)",
    ):
        op.execute(sql)
    # Deterministic views. Ratios are 0..1, not percentage points. Keep this
    # migration aligned with the reviewable SQL files under sql/views/.
    for v in [
        "facts_provider_tables",
        "analytics_project_full",
        "analytics_tax",
        "analytics_eac",
        "analytics_cost",
        "analytics_cashflow",
        "analytics_project_profit",
        "analytics_project_summary",
    ]:
        op.execute(text(f"DROP VIEW IF EXISTS {v} CASCADE"))
    op.execute(
        text(r"""CREATE VIEW analytics_project_summary AS
      SELECT p.id project_id, COALESCE(p.project_code,p.code) project_code,p.name project_name,p.entity_code,
      COALESCE(NULLIF(p.status,''),'ACTIVE') status,COALESCE(p.contract_amount,p.contract_total) contract_amount,
      p.start_date,p.expected_end_date,COALESCE(NULLIF(p.location,''),p.city) location,p.project_type,
      p.external_system,p.external_project_id,CURRENT_TIMESTAMP calculated_at
      FROM projects p""")
    )
    op.execute(
        text(r"""CREATE VIEW analytics_project_profit AS
      WITH pr AS (SELECT project_id,SUM(recognized_revenue) recognized_revenue,SUM(collection) collected_amount FROM progress GROUP BY project_id),
      rc AS (SELECT project_id,SUM(amount) real_project_cost FROM real_costs GROUP BY project_id)
      SELECT p.project_id,p.project_code,p.project_name,p.entity_code,p.contract_amount,
      pr.recognized_revenue,rc.real_project_cost,
      CASE WHEN pr.recognized_revenue IS NULL OR rc.real_project_cost IS NULL THEN NULL ELSE pr.recognized_revenue-rc.real_project_cost END real_profit,
      pr.collected_amount,CASE WHEN pr.recognized_revenue>0 THEN pr.collected_amount/pr.recognized_revenue ELSE NULL END collection_rate,
      CASE WHEN pr.recognized_revenue IS NULL OR pr.collected_amount IS NULL THEN NULL ELSE pr.recognized_revenue-pr.collected_amount END unpaid_amount,
      CURRENT_TIMESTAMP calculated_at
      FROM analytics_project_summary p LEFT JOIN pr ON pr.project_id=p.project_id LEFT JOIN rc ON rc.project_id=p.project_id""")
    )
    op.execute(
        text(r"""CREATE VIEW analytics_cost AS
      WITH a AS (SELECT project_id,SUM(amount) actual_cost FROM real_costs GROUP BY project_id),
      b AS (SELECT project_id,SUM(amount) budget_cost FROM budgets GROUP BY project_id)
      SELECT p.project_id,p.project_code,p.project_name,a.actual_cost,b.budget_cost,
      CASE WHEN a.actual_cost IS NULL OR b.budget_cost IS NULL THEN NULL ELSE a.actual_cost-b.budget_cost END cost_variance,
      CASE WHEN b.budget_cost>0 AND a.actual_cost IS NOT NULL THEN (a.actual_cost-b.budget_cost)/b.budget_cost ELSE 0 END cost_variance_rate,
      CASE WHEN a.actual_cost>0 AND b.budget_cost IS NOT NULL THEN b.budget_cost/a.actual_cost ELSE 1.0 END cpi,CURRENT_TIMESTAMP calculated_at
      FROM analytics_project_summary p LEFT JOIN a ON a.project_id=p.project_id LEFT JOIN b ON b.project_id=p.project_id""")
    )
    op.execute(
        text(r"""CREATE VIEW analytics_eac AS
      WITH budget AS (
        SELECT b.project_id, SUM(b.amount) AS total_budget_cost
        FROM budgets b GROUP BY b.project_id
      ), inputs AS (
        SELECT p.project_id,p.project_code,p.project_name,p.contract_amount,
               pp.recognized_revenue,pp.real_project_cost actual_cost,
               b.total_budget_cost,
               CASE WHEN b.total_budget_cost IS NOT NULL AND pp.real_project_cost>0
                    THEN b.total_budget_cost/pp.real_project_cost ELSE NULL END cpi
        FROM analytics_project_summary p
        LEFT JOIN analytics_project_profit pp
          USING(project_id,project_code,project_name,contract_amount)
        LEFT JOIN budget b ON b.project_id=p.project_id
      )
      SELECT project_id,project_code,project_name,contract_amount,
      recognized_revenue,actual_cost,
      CASE WHEN contract_amount>0 AND recognized_revenue>0 AND actual_cost IS NOT NULL
           THEN actual_cost/(recognized_revenue/contract_amount) ELSE NULL END eac_cost,
      contract_amount eac_revenue,
      CASE WHEN contract_amount>0 AND recognized_revenue>0 AND actual_cost IS NOT NULL
           THEN contract_amount-(actual_cost/(recognized_revenue/contract_amount)) ELSE NULL END eac_profit,
      CASE WHEN contract_amount>0 AND recognized_revenue>0 AND actual_cost IS NOT NULL
           THEN (contract_amount-(actual_cost/(recognized_revenue/contract_amount)))/contract_amount ELSE NULL END eac_margin,
      'management_estimate_revenue_progress'::text eac_method,CURRENT_TIMESTAMP calculated_at
      FROM inputs""")
    )
    op.execute(
        text(r"""CREATE VIEW analytics_cashflow AS
      WITH cf AS (SELECT project_id,SUM(CASE WHEN direction='in' THEN amount ELSE 0 END) cash_inflow,
                         SUM(CASE WHEN direction='out' THEN amount ELSE 0 END) cash_outflow FROM cashflows GROUP BY project_id)
      SELECT p.project_id,p.project_code,p.project_name,cf.cash_inflow,cf.cash_outflow,
      CASE WHEN cf.cash_inflow IS NULL AND cf.cash_outflow IS NULL THEN NULL ELSE COALESCE(cf.cash_inflow,0)-COALESCE(cf.cash_outflow,0) END net_cashflow,
      NULL::numeric cash_gap_30d,CURRENT_TIMESTAMP calculated_at
      FROM analytics_project_summary p LEFT JOIN cf ON cf.project_id=p.project_id""")
    )
    op.execute(
        text(r"""CREATE VIEW analytics_tax AS
      WITH r AS (
        SELECT project_id,SUM(recognized_revenue) AS taxable_revenue
        FROM progress GROUP BY project_id
      ), iv AS (
        SELECT project_id,SUM(CASE WHEN direction='out' THEN vat ELSE 0 END) output_vat,
               SUM(CASE WHEN direction='in' AND deductible THEN vat ELSE 0 END) input_vat
        FROM invoices GROUP BY project_id
      )
      SELECT p.project_id,p.project_code,p.project_name,p.entity_code,
      iv.output_vat,iv.input_vat,
      CASE WHEN iv.output_vat IS NULL AND iv.input_vat IS NULL THEN NULL ELSE GREATEST(COALESCE(iv.output_vat,0)-COALESCE(iv.input_vat,0),0) END vat_payable,
      NULL::numeric income_tax_estimate,NULL::numeric surcharge_estimate,
      CASE WHEN r.taxable_revenue IS NOT NULL THEN NULL::numeric ELSE NULL::numeric END tax_burden_rate,
      NULL::numeric total_tax_burden,CURRENT_TIMESTAMP calculated_at
      FROM analytics_project_summary p
      LEFT JOIN r ON r.project_id=p.project_id
      LEFT JOIN iv ON iv.project_id=p.project_id""")
    )
    op.execute(
        text(r"""CREATE VIEW analytics_project_full AS
      SELECT analytics_project_full.project_id,analytics_project_full.project_code,
      analytics_project_full.project_name,analytics_project_full.entity_code,
      analytics_project_full.status,analytics_project_full.contract_amount,
      analytics_project_full.location,analytics_project_full.recognized_revenue,
      analytics_project_full.real_project_cost,analytics_project_full.real_profit,
      analytics_project_full.collected_amount,analytics_project_full.unpaid_amount,
      analytics_project_full.collection_rate,analytics_project_full.eac_revenue,
      analytics_project_full.eac_cost,analytics_project_full.eac_profit,
      analytics_project_full.eac_margin,analytics_project_full.eac_method,
      analytics_project_full.cost_variance,analytics_project_full.cash_inflow,
      analytics_project_full.cash_outflow,analytics_project_full.net_cashflow,
      analytics_project_full.cash_gap_30d,analytics_project_full.output_vat,
      analytics_project_full.input_vat,analytics_project_full.vat_payable,
      analytics_project_full.tax_burden_rate,analytics_project_full.health_score,
      analytics_project_full.facts_available,analytics_project_full.calculated_at
      FROM (
        SELECT s.project_id,s.project_code,s.project_name,s.entity_code,s.status,
        s.contract_amount,s.location,
        pp.recognized_revenue,pp.real_project_cost,pp.real_profit,
        pp.collected_amount,pp.unpaid_amount,pp.collection_rate,
        e.eac_revenue,e.eac_cost,e.eac_profit,e.eac_margin,e.eac_method,
        c.cost_variance,cf.cash_inflow,cf.cash_outflow,cf.net_cashflow,
        cf.cash_gap_30d,t.output_vat,t.input_vat,t.vat_payable,
        t.tax_burden_rate,
        CASE
          WHEN pp.recognized_revenue IS NULL OR pp.real_project_cost IS NULL
            OR e.eac_profit IS NULL THEN NULL::numeric
          ELSE NULL::numeric
        END health_score,
        (pp.recognized_revenue IS NOT NULL AND pp.real_project_cost IS NOT NULL
         AND pp.collection_rate IS NOT NULL AND e.eac_profit IS NOT NULL
         AND e.eac_margin IS NOT NULL) facts_available,
        CURRENT_TIMESTAMP calculated_at
        FROM analytics_project_summary s
        LEFT JOIN analytics_project_profit pp
          USING(project_id,project_code,project_name,entity_code,contract_amount)
        LEFT JOIN analytics_eac e
          USING(project_id,project_code,project_name,contract_amount)
        LEFT JOIN analytics_cost c
          USING(project_id,project_code,project_name)
        LEFT JOIN analytics_cashflow cf
          USING(project_id,project_code,project_name)
        LEFT JOIN analytics_tax t
          USING(project_id,project_code,project_name,entity_code)
      ) AS analytics_project_full""")
    )
    op.execute(
        text(r"""CREATE VIEW facts_provider_tables AS
      SELECT 'facts_snapshots'::text AS table_name
      UNION ALL SELECT 'facts_request_logs'::text AS table_name""")
    )


def downgrade():
    for v in [
        "facts_provider_tables",
        "analytics_project_full",
        "analytics_tax",
        "analytics_eac",
        "analytics_cost",
        "analytics_cashflow",
        "analytics_project_profit",
        "analytics_project_summary",
    ]:
        op.execute(text(f"DROP VIEW IF EXISTS {v} CASCADE"))
