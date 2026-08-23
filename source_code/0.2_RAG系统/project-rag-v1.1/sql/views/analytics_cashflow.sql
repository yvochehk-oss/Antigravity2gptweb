-- Posted project cashflows only. No fabricated forward-looking 30-day gap.
CREATE OR REPLACE VIEW analytics_cashflow AS
WITH cf AS (
  SELECT project_id,
         SUM(CASE WHEN direction='in' THEN amount ELSE 0 END) AS cash_inflow,
         SUM(CASE WHEN direction='out' THEN amount ELSE 0 END) AS cash_outflow
  FROM cashflows GROUP BY project_id
)
SELECT p.project_id,p.project_code,p.project_name,cf.cash_inflow,cf.cash_outflow,
       CASE WHEN cf.cash_inflow IS NULL AND cf.cash_outflow IS NULL THEN NULL
            ELSE COALESCE(cf.cash_inflow,0)-COALESCE(cf.cash_outflow,0) END AS net_cashflow,
       NULL::numeric AS cash_gap_30d,CURRENT_TIMESTAMP AS calculated_at
FROM analytics_project_summary p LEFT JOIN cf ON cf.project_id=p.project_id;
