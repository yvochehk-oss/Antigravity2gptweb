# 内部交易关系 (Internal Transaction)

## 基本信息

- relationship_id: internal_transaction
- version: 1.0

## 定义

实体主数据中两个真实内部法人实体之间的交易关系，需要在 analytics 层进行抵消处理。

## 涉及实体

内部交易双方必须是 Canonical Entity Master 中的实际 `entity_code`（当前范围 `A01`–`A11`、`B01`–`B10`、`C01`–`C02`、`D01`–`D03`），且在交易发生时均为 active legal entity。`business_role` 只用于分组和规则解释，不能单独判定交易是否内部。

内部交易类型:

- 设备租赁
- 材料调拨
- 服务提供

## 抵消逻辑

```
内部交易发生时：
  1. 记录原始交易（例如 `A08` 四川锐宝建设工程有限公司开票给 `B01` 四川乾润和贸易有限公司）
  2. 在 analytics 层抵消：
     - 收入侧：减少内部收入
     - 成本侧：恢复底层真实经济成本
  3. 最终影响：
     - 合并报表层面只看外部交易
     - 内部企业底层保持真实经济记录
```

## 抵消字段

- internal_elimination_revenue: 需抵消的内部收入
- internal_elimination_cost: 需抵消的内部成本
- real_economic_cost: 底层真实经济成本（用于恢复）

## 抵消公式

analytics_cost = 原始成本 + real_economic_cost - internal_elimination_cost
