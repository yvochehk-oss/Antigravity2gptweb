# 回款率

## 基本信息

- metric_id: collection_rate
- version: 1.2
- name_cn: 回款率
- name_en: Collection Rate
- owner: finance
- approved_by: CFO
- effective_from: 2026-08-01
- effective_to: null
- deprecates: 1.1

## 定义

已确认收入中已收回现金的比例，反映项目资金回收效率。

## 计算公式

```
回款率 = 已收款金额 / 确认收入金额
```

### 分母说明

- 分母使用 recognized_revenue（确认收入），而非合同总额
- 确认收入 = 实际完成工程量对应的产值

## 数据粒度

- grain: project
- 时间维度: 月度累计

## 数据来源

- source_view: analytics_project_profit
- source_columns:
  - collected_amount: 已收款金额
  - recognized_revenue: 确认收入金额

## 计算字段

collection_rate = collected_amount / recognized_revenue

## 版本变更历史

### v1.2 (2026-08-01)

- change_reason: 明确分母为确认收入而非合同总额

### v1.1 (2026-01-01) [已废弃]

### v1.0 [已废弃]

## 验证规则

- collection_rate 范围：0% - 100%+
- 超过 100% 可能表示预付款或结算调整
- collection_rate 小于 0.5 需触发回款预警
- collection_rate 小于 0.3 需触发重大回款风险预警

## 使用场景

- 现金流分析
- 回款趋势分析（月度/季度趋势）
- AI Review 现金流判断
