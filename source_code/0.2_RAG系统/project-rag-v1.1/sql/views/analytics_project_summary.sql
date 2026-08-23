-- PostgreSQL-only reference. Alembic is the schema source of truth.
CREATE OR REPLACE VIEW analytics_project_summary AS
SELECT p.id AS project_id, COALESCE(p.project_code,p.code) AS project_code, p.name AS project_name,
       p.entity_code, COALESCE(NULLIF(p.status,''),'ACTIVE') AS status,
       COALESCE(p.contract_amount,p.contract_total) AS contract_amount, p.start_date, p.expected_end_date,
       COALESCE(NULLIF(p.location,''),p.city) AS location, p.project_type, p.external_system, p.external_project_id,
       CURRENT_TIMESTAMP AS calculated_at
FROM projects p;
