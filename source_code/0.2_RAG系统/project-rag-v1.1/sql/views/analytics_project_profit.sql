-- Deterministic project profit from Tax truth tables. Ratios are 0..1.
CREATE OR REPLACE VIEW analytics_project_profit AS
WITH pr AS (
  SELECT project_id, SUM(recognized_revenue) AS recognized_revenue, SUM(collection) AS collected_amount
  FROM progress GROUP BY project_id
), rc AS (
  SELECT project_id, SUM(amount) AS real_project_cost FROM real_costs GROUP BY project_id
)
SELECT p.project_id,p.project_code,p.project_name,p.entity_code,p.contract_amount,
       pr.recognized_revenue,rc.real_project_cost,
       CASE WHEN pr.recognized_revenue IS NULL OR rc.real_project_cost IS NULL THEN NULL
            ELSE pr.recognized_revenue-rc.real_project_cost END AS real_profit,
       pr.collected_amount,
       CASE WHEN pr.recognized_revenue>0 THEN pr.collected_amount/pr.recognized_revenue ELSE NULL END AS collection_rate,
       CASE WHEN pr.recognized_revenue IS NULL OR pr.collected_amount IS NULL THEN NULL
            ELSE pr.recognized_revenue-pr.collected_amount END AS unpaid_amount,
       CURRENT_TIMESTAMP AS calculated_at
FROM analytics_project_summary p
LEFT JOIN pr ON pr.project_id=p.project_id
LEFT JOIN rc ON rc.project_id=p.project_id;
