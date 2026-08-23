-- VAT is computed from posted invoices. CIT/surcharge/full burden stay NULL until deterministic allocation exists.
CREATE OR REPLACE VIEW analytics_tax AS
WITH iv AS (
  SELECT project_id,
         SUM(CASE WHEN direction='out' THEN vat ELSE 0 END) AS output_vat,
         SUM(CASE WHEN direction='in' AND deductible THEN vat ELSE 0 END) AS input_vat
  FROM invoices GROUP BY project_id
)
SELECT p.project_id,p.project_code,p.project_name,p.entity_code,iv.output_vat,iv.input_vat,
       CASE WHEN iv.output_vat IS NULL AND iv.input_vat IS NULL THEN NULL
            ELSE GREATEST(COALESCE(iv.output_vat,0)-COALESCE(iv.input_vat,0),0) END AS vat_payable,
       NULL::numeric AS income_tax_estimate,NULL::numeric AS surcharge_estimate,
       NULL::numeric AS tax_burden_rate,NULL::numeric AS total_tax_burden,CURRENT_TIMESTAMP AS calculated_at
FROM analytics_project_summary p LEFT JOIN iv ON iv.project_id=p.project_id;
