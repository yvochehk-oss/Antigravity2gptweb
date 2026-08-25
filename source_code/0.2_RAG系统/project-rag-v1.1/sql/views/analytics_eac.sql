-- Management EAC estimate. It uses revenue recognition progress as a proxy
-- and is not certified engineering progress.  Budget/CPI are exposed only as
-- optional context; they do not replace the approved revenue-progress EAC
-- formula and stay NULL when the underlying source is absent.
CREATE OR REPLACE VIEW analytics_eac AS
WITH budget AS (
  SELECT b.project_id, SUM(b.amount) AS total_budget_cost
  FROM budgets b
  GROUP BY b.project_id
), inputs AS (
  SELECT p.project_id,p.project_code,p.project_name,p.contract_amount,
         pp.recognized_revenue,pp.real_project_cost AS actual_cost,
         -- Keep the optional budget/CPI context inside the calculation
         -- boundary.  It is not promoted to the public EAC row until a
         -- versioned view schema can be deployed without breaking readers.
         b.total_budget_cost,
         CASE WHEN b.total_budget_cost IS NOT NULL AND pp.real_project_cost>0
              THEN b.total_budget_cost/pp.real_project_cost ELSE NULL END AS cpi
  FROM analytics_project_summary p
  LEFT JOIN analytics_project_profit pp
    USING(project_id,project_code,project_name,contract_amount)
  LEFT JOIN budget b ON b.project_id=p.project_id
)
SELECT project_id,project_code,project_name,contract_amount,
       recognized_revenue,actual_cost,
       CASE WHEN contract_amount>0 AND recognized_revenue>0 AND actual_cost IS NOT NULL
            THEN actual_cost/(recognized_revenue/contract_amount) ELSE NULL END AS eac_cost,
       contract_amount AS eac_revenue,
       CASE WHEN contract_amount>0 AND recognized_revenue>0 AND actual_cost IS NOT NULL
            THEN contract_amount-(actual_cost/(recognized_revenue/contract_amount)) ELSE NULL END AS eac_profit,
       CASE WHEN contract_amount>0 AND recognized_revenue>0 AND actual_cost IS NOT NULL
            THEN (contract_amount-(actual_cost/(recognized_revenue/contract_amount)))/contract_amount ELSE NULL END AS eac_margin,
       'management_estimate_revenue_progress'::text AS eac_method,
       CURRENT_TIMESTAMP AS calculated_at
FROM inputs;
