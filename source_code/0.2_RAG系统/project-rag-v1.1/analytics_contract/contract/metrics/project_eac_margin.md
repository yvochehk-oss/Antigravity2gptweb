# 项目 EAC 利润率（管理估算口径）

- metric_id: project_eac_margin
- version: 2.0
- owner: finance
- source_view: analytics_eac
- unit: ratio (0..1)

`eac_margin = eac_profit / eac_revenue`。

EAC 上游采用 `management_estimate_revenue_progress` 管理估算，详见 `project_eac_profit.md`。集团层面不得简单平均项目利润率，应使用 `SUM(eac_profit) / SUM(eac_revenue)`。

没有可靠 EAC 上游时返回 `NULL`，不得以 0 代替。
