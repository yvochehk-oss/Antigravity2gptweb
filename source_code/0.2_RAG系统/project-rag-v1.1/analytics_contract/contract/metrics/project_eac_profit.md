# 项目 EAC 预计利润（管理估算口径）

- metric_id: project_eac_profit
- version: 2.0
- owner: finance
- source_view: analytics_eac
- eac_method: management_estimate_revenue_progress

## 当前口径

当前系统没有独立、经审核的工程“挣值/完工百分比”事实，因此**不再伪造 BAC/CPI/EVM 数据**。本阶段 EAC 仅作为管理估算：

1. `progress_proxy = recognized_revenue / contract_amount`
2. `eac_cost = actual_real_cost / progress_proxy`
3. `eac_revenue = contract_amount`
4. `eac_profit = eac_revenue - eac_cost`

仅当合同额、确认收入、真实成本均为有效正值时计算；否则返回 `NULL`。

## 限制

该指标不是工程造价/挣值法正式 EAC。后续如果引入经审核的工程量、预算成本与完工进度，应新增更高版本指标，不得静默替换本口径。
