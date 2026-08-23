# 30天现金缺口

- metric_id: cash_gap_30d
- version: 2.0
- status: pending_deterministic_source
- owner: finance

## 定义

该指标需要“当前可用现金 + 未来30天计划流入 + 未来30天计划流出”三个确定性数据源。当前共享 PostgreSQL 只具备**已发生现金流**，尚未建立经审核的期初资金余额和未来收支计划表。

因此当前 `analytics_cashflow.cash_gap_30d` 必须返回 `NULL`，不得用历史现金流、成本差异或固定默认值代替。

## 启用条件

只有在后续建立并审核资金余额/计划收支数据模型后，才能恢复公式：

`cash_gap_30d = max(planned_outflow_30d - planned_inflow_30d - available_cash, 0)`

在此之前，AI Review 只能报告“预测资金数据不足”，不能给出资金缺口金额。
