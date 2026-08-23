"""Transactionally synchronize Tax/RAG alias columns.
Revision ID: 59d2_shared_alias_triggers
Revises: 58c1_postgresql_shared_master
"""
from alembic import op
revision='59d2_shared_alias_triggers'; down_revision='58c1_postgresql_shared_master'; branch_labels=None; depends_on=None
PROJECT_FN=r"""
CREATE OR REPLACE FUNCTION sync_project_aliases() RETURNS trigger AS $$
BEGIN
  IF NEW.code IS NOT NULL AND NEW.project_code IS NOT NULL AND NEW.code <> NEW.project_code THEN RAISE EXCEPTION 'projects code/project_code conflict'; END IF;
  NEW.code := COALESCE(NEW.code, NEW.project_code); NEW.project_code := COALESCE(NEW.project_code, NEW.code);
  IF NEW.contract_total IS NOT NULL AND NEW.contract_amount IS NOT NULL AND NEW.contract_total <> NEW.contract_amount THEN RAISE EXCEPTION 'projects contract amount conflict'; END IF;
  NEW.contract_total := COALESCE(NEW.contract_total, NEW.contract_amount); NEW.contract_amount := COALESCE(NEW.contract_amount, NEW.contract_total);
  IF COALESCE(NEW.city,'')<>'' AND COALESCE(NEW.location,'')<>'' AND NEW.city<>NEW.location THEN RAISE EXCEPTION 'projects city/location conflict'; END IF;
  NEW.city := COALESCE(NULLIF(NEW.city,''), NEW.location, ''); NEW.location := COALESCE(NULLIF(NEW.location,''), NEW.city, '');
  NEW.tax_method := COALESCE(NULLIF(NEW.tax_method,''), 'general');
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_sync_project_aliases ON projects;
CREATE TRIGGER trg_sync_project_aliases BEFORE INSERT OR UPDATE ON projects FOR EACH ROW EXECUTE FUNCTION sync_project_aliases();
"""
ENTITY_FN=r"""
CREATE OR REPLACE FUNCTION sync_entity_aliases() RETURNS trigger AS $$
BEGIN
  IF NEW.code IS NOT NULL AND NEW.entity_code IS NOT NULL AND NEW.code <> NEW.entity_code THEN RAISE EXCEPTION 'entities code/entity_code conflict'; END IF;
  NEW.code := COALESCE(NEW.code, NEW.entity_code); NEW.entity_code := COALESCE(NEW.entity_code, NEW.code);
  NEW.entity_kind := COALESCE(NULLIF(NEW.entity_kind,''), NULLIF(NEW.kind,''), 'company'); NEW.kind := COALESCE(NULLIF(NEW.kind,''), NEW.entity_kind, 'company');
  NEW.internal := COALESCE(NEW.internal, TRUE);
  IF NEW.status IS NULL OR NEW.status='' THEN NEW.status := CASE WHEN COALESCE(NEW.active,TRUE) THEN 'active' ELSE 'inactive' END;
  ELSE NEW.active := lower(NEW.status)='active'; END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_sync_entity_aliases ON entities;
CREATE TRIGGER trg_sync_entity_aliases BEFORE INSERT OR UPDATE ON entities FOR EACH ROW EXECUTE FUNCTION sync_entity_aliases();
"""
def upgrade():
    if op.get_bind().dialect.name!='postgresql': raise RuntimeError('PostgreSQL-only migration')
    op.execute(PROJECT_FN); op.execute(ENTITY_FN)
def downgrade():
    op.execute('DROP TRIGGER IF EXISTS trg_sync_project_aliases ON projects'); op.execute('DROP FUNCTION IF EXISTS sync_project_aliases()')
    op.execute('DROP TRIGGER IF EXISTS trg_sync_entity_aliases ON entities'); op.execute('DROP FUNCTION IF EXISTS sync_entity_aliases()')
