# 项目综合真实利润

## 基本信息

- metric_id: project_real_profit
- version: 2.1
- name_cn: 项目综合真实利润
- name_en: Project Real Profit
- owner: finance
- approved_by: CFO
- effective_from: 2026-10-01
- effective_to: null
- deprecates: 2.0

## 定义

项目层面合并内部交易后的真实经济利润，不等于任何单一法律实体的账面利润。项目、收入、成本和内部交易均按 Canonical Entity Master 中的真实 `entity_code` 归属；`business_role` 只能用于汇总分组。

## 计算公式

```
项目综合真实利润 = 确认收入 - 真实项目成本（内部抵消后）
```

### 内部交易抵消规则

- 系统内部边界由两端的真实 `entity_code` 是否解析为 active legal entity 决定，不能用单字业务角色代替。
- 内部交易时：收入成本抵消 + 恢复交易链条底层真实经济成本。
- 例如 `A08` 与 `B01` 之间的已确认内部交易进入抵消流程；外部交易对手始终按外部经济成本处理，不做实体穿透。

## 数据粒度

- grain: project
- 时间维度: 月度/季度/年度

## 数据来源

- source_view: analytics_project_profit
- source_columns:
  - recognized_revenue: 确认收入
  - real_project_cost: 真实项目成本（含内部抵消）
  - internal_elimination_amount: 内部抵消金额

## 计算字段

real_profit = recognized_revenue - real_project_cost

## 版本变更历史

### v2.1 (2026-10-01)

- change_reason: 调整内部设备租赁抵消后的真实成本口径
- formula_engine: profit_engine_3.2

### v2.0 (2026-06-01) [已废弃]

- 原始定义，基础抵消规则

### v1.x [已废弃]

- 未包含内部交易抵消

## 验证规则

- real_profit 可以为负数（亏损项目）
- real_profit 不应大于 recognized_revenue（利润不能超过收入）
- 内部抵消后成本应小于等于原始成本

## 使用场景

- 项目经营分析
- AI Review 综合判断
- 财务报表生成

## 依赖指标

- 无依赖其他自定义指标
- 依赖底层业务数据：收入、成本、内部交易记录
