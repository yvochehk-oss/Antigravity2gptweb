-- Canonical Facts surface. Optional unknown metrics remain NULL and do not poison deterministic core facts.
CREATE OR REPLACE VIEW analytics_project_full AS
SELECT s.project_id,s.project_code,s.project_name,s.entity_code,s.status,s.contract_amount,s.location,
       pp.recognized_revenue,pp.real_project_cost,pp.real_profit,pp.collected_amount,pp.unpaid_amount,pp.collection_rate,
       e.eac_revenue,e.eac_cost,e.eac_profit,e.eac_margin,e.eac_method,
       c.cost_variance,cf.cash_inflow,cf.cash_outflow,cf.net_cashflow,cf.cash_gap_30d,
       t.output_vat,t.input_vat,t.vat_payable,t.tax_burden_rate,NULL::numeric AS health_score,
       (pp.recognized_revenue IS NOT NULL AND pp.real_project_cost IS NOT NULL AND pp.collection_rate IS NOT NULL
        AND e.eac_profit IS NOT NULL AND e.eac_margin IS NOT NULL) AS facts_available,
       CURRENT_TIMESTAMP AS calculated_at
FROM analytics_project_summary s
LEFT JOIN analytics_project_profit pp USING(project_id,project_code,project_name,entity_code,contract_amount)
LEFT JOIN analytics_eac e USING(project_id,project_code,project_name,contract_amount)
LEFT JOIN analytics_cost c USING(project_id,project_code,project_name)
LEFT JOIN analytics_cashflow cf USING(project_id,project_code,project_name)
LEFT JOIN analytics_tax t USING(project_id,project_code,project_name,entity_code);
