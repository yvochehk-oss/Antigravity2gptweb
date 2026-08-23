# 成本偏差率

## 基本信息

- metric_id: cost_variance
- version: 1.0
- name_cn: 成本偏差率
- name_en: Cost Variance Rate
- owner: project_control
- approved_by: PMO
- effective_from: 2026-08-01
- effective_to: null

## 定义

实际成本与预算成本的偏差比例，用于衡量成本控制水平。

## 计算公式

```
成本偏差率 = (实际成本 - 预算成本) / 预算成本
```

### SPI 辅助指标

成本绩效指数 (CPI) = 完工预算 / 实际成本

## 数据粒度

- grain: project + cost_category
- 时间维度: 月度累计

## 数据来源

- source_view: analytics_cost
- source_columns:
  - actual_cost: 实际成本
  - budget_cost: 预算成本

## 计算字段

- cost_variance_rate = (actual_cost - budget_cost) / budget_cost
- cpi = budget_cost / actual_cost

## 正负含义

- 大于 0：超支
- 等于 0：按预算
- 小于 0：节约

## 验证规则

- cost_variance_rate 大于 0.05 需触发成本超支预警
- cost_variance_rate 大于 0.15 需触发重大风险预警
- cpi 小于 0.8 需触发成本绩效预警

## 使用场景

- 成本控制分析
- 项目绩效评估
- AI Review 成本判断
