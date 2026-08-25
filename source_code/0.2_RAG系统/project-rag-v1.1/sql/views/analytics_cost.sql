-- Actual cost vs budget from canonical Tax tables.
CREATE OR REPLACE VIEW analytics_cost AS
WITH a AS (SELECT project_id,SUM(amount) AS actual_cost FROM real_costs GROUP BY project_id),
     b AS (SELECT project_id,SUM(amount) AS budget_cost FROM budgets GROUP BY project_id)
SELECT p.project_id,p.project_code,p.project_name,a.actual_cost,b.budget_cost,
       CASE WHEN a.actual_cost IS NULL OR b.budget_cost IS NULL THEN NULL ELSE a.actual_cost-b.budget_cost END AS cost_variance,
       -- The rate and CPI are optional management indicators.  A missing
       -- source is represented by a neutral baseline (0 / 1) here; the
       -- canonical Facts view does not use either value to mark core Facts
       -- available, and never turns a missing amount into a financial total.
       CASE WHEN b.budget_cost>0 AND a.actual_cost IS NOT NULL THEN (a.actual_cost-b.budget_cost)/b.budget_cost ELSE 0 END AS cost_variance_rate,
       CASE WHEN a.actual_cost>0 AND b.budget_cost IS NOT NULL THEN b.budget_cost/a.actual_cost ELSE 1.0 END AS cpi,
       CURRENT_TIMESTAMP AS calculated_at
FROM analytics_project_summary p LEFT JOIN a ON a.project_id=p.project_id LEFT JOIN b ON b.project_id=p.project_id;
