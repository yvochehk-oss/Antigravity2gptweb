-- PostgreSQL-only reference. Alembic is the schema source of truth.
CREATE OR REPLACE VIEW analytics_project_summary AS
WITH entity_master AS (
  SELECT entity_code,
         COUNT(*) AS master_rows,
         COUNT(*) FILTER (
           WHERE LOWER(BTRIM(COALESCE(status, ''))) = 'active'
         ) AS active_rows
  FROM entities
  GROUP BY entity_code
)
SELECT p.id AS project_id, COALESCE(p.project_code,p.code) AS project_code, p.name AS project_name,
       p.entity_code, COALESCE(NULLIF(p.status,''),'ACTIVE') AS status,
       COALESCE(p.contract_amount,p.contract_total) AS contract_amount, p.start_date, p.expected_end_date,
       COALESCE(NULLIF(p.location,''),p.city) AS location, p.project_type, p.external_system, p.external_project_id,
       CURRENT_TIMESTAMP AS calculated_at,
       CASE
         WHEN NULLIF(BTRIM(p.entity_code), '') IS NULL THEN 'MISSING'
         WHEN UPPER(BTRIM(p.entity_code)) !~ '^(A(0[1-9]|1[01])|B(0[1-9]|10)|C0[12]|D0[1-3])$'
           THEN 'INVALID'
         WHEN e.entity_code IS NULL THEN 'UNRESOLVED'
         WHEN e.master_rows <> 1 THEN 'AMBIGUOUS'
         WHEN e.active_rows <> 1 THEN 'INACTIVE'
         ELSE 'VALID'
       END AS entity_mapping_status,
       CASE
         WHEN NULLIF(BTRIM(p.entity_code), '') IS NULL
           THEN 'project.entity_code is NULL or blank'
         WHEN UPPER(BTRIM(p.entity_code)) !~ '^(A(0[1-9]|1[01])|B(0[1-9]|10)|C0[12]|D0[1-3])$'
           THEN 'project.entity_code is not a canonical numbered entity code'
         WHEN e.entity_code IS NULL
           THEN 'no matching canonical entity in entities master'
         WHEN e.master_rows <> 1
           THEN 'canonical entity code is not unique in entities master'
         WHEN e.active_rows <> 1
           THEN 'canonical entity exists but is not active'
         ELSE NULL
       END AS entity_mapping_reason,
       (
         NULLIF(BTRIM(p.entity_code), '') IS NOT NULL
         AND UPPER(BTRIM(p.entity_code)) ~ '^(A(0[1-9]|1[01])|B(0[1-9]|10)|C0[12]|D0[1-3])$'
         AND e.entity_code IS NOT NULL
         AND e.master_rows = 1
         AND e.active_rows = 1
       ) AS entity_mapping_valid
FROM projects p
LEFT JOIN entity_master e
  ON e.entity_code = UPPER(BTRIM(p.entity_code));
