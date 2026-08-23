-- Management EAC estimate. It uses revenue recognition progress as a proxy and is not certified engineering progress.
CREATE OR REPLACE VIEW analytics_eac AS
SELECT p.project_id,p.project_code,p.project_name,p.contract_amount,pp.recognized_revenue,pp.real_project_cost AS actual_cost,
       CASE WHEN p.contract_amount>0 AND pp.recognized_revenue>0 AND pp.real_project_cost IS NOT NULL
            THEN pp.real_project_cost/(pp.recognized_revenue/p.contract_amount) ELSE NULL END AS eac_cost,
       p.contract_amount AS eac_revenue,
       CASE WHEN p.contract_amount>0 AND pp.recognized_revenue>0 AND pp.real_project_cost IS NOT NULL
            THEN p.contract_amount-(pp.real_project_cost/(pp.recognized_revenue/p.contract_amount)) ELSE NULL END AS eac_profit,
       CASE WHEN p.contract_amount>0 AND pp.recognized_revenue>0 AND pp.real_project_cost IS NOT NULL
            THEN (p.contract_amount-(pp.real_project_cost/(pp.recognized_revenue/p.contract_amount)))/p.contract_amount ELSE NULL END AS eac_margin,
       'management_estimate_revenue_progress'::text AS eac_method,CURRENT_TIMESTAMP AS calculated_at
FROM analytics_project_summary p LEFT JOIN analytics_project_profit pp USING(project_id,project_code,project_name,contract_amount);
