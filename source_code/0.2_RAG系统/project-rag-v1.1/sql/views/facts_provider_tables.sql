-- PostgreSQL-only metadata surface for Facts persistence tables.
-- Production schema is created and upgraded only by Alembic; application
-- startup must not call create_all().  The view intentionally exposes names,
-- not a second copy of either table's data.
CREATE OR REPLACE VIEW facts_provider_tables AS
SELECT 'facts_snapshots'::text AS table_name
UNION ALL
SELECT 'facts_request_logs'::text AS table_name;

-- The physical tables are created by alembic/versions/001_initial.py and
-- the canonical shared contract is validated by 008_shared_facts_snapshot_contract.py.
