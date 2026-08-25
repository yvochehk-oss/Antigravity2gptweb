-- Canonical Facts surface. Optional unknown metrics remain NULL and do not
-- poison deterministic core facts.  health_score has no approved
-- deterministic source in this schema, so its explicit ELSE NULL branch is
-- intentionally unavailable rather than a fabricated score.
CREATE OR REPLACE VIEW analytics_project_full AS
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
       analytics_project_full.facts_available,analytics_project_full.calculated_at,
       analytics_project_full.entity_mapping_status,
       analytics_project_full.entity_mapping_reason,
       analytics_project_full.entity_mapping_valid
FROM (
  SELECT s.project_id,s.project_code,s.project_name,s.entity_code,s.status,
         s.contract_amount,s.location,
         pp.recognized_revenue,pp.real_project_cost,pp.real_profit,
         pp.collected_amount,pp.unpaid_amount,pp.collection_rate,
         e.eac_revenue,e.eac_cost,e.eac_profit,e.eac_margin,e.eac_method,
         c.cost_variance,cf.cash_inflow,cf.cash_outflow,cf.net_cashflow,
         cf.cash_gap_30d,t.output_vat,t.input_vat,t.vat_payable,
         t.tax_burden_rate,
         s.entity_mapping_status,s.entity_mapping_reason,s.entity_mapping_valid,
         CASE
           WHEN pp.recognized_revenue IS NULL
             OR pp.real_project_cost IS NULL
             OR e.eac_profit IS NULL
           THEN NULL::numeric
           ELSE NULL::numeric
         END AS health_score,
         (pp.recognized_revenue IS NOT NULL
          AND pp.real_project_cost IS NOT NULL
         AND pp.collection_rate IS NOT NULL
         AND e.eac_profit IS NOT NULL
         AND e.eac_margin IS NOT NULL
         AND s.entity_mapping_valid) AS facts_available,
         CURRENT_TIMESTAMP AS calculated_at
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
) AS analytics_project_full;
