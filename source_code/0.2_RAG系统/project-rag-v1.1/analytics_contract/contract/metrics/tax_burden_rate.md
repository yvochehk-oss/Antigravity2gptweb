# 综合税负率

- metric_id: tax_burden_rate
- version: 2.0
- status: pending_deterministic_allocation
- owner: tax

## 当前状态

当前项目级确定性数据可以从 `invoices` 计算销项 VAT、可抵扣进项 VAT 和项目级 VAT 应纳估计。

但企业所得税、附加税及跨主体/跨项目分摊尚没有一套经审核、可追溯的项目级确定性分摊事实，因此 `analytics_tax.tax_burden_rate` 当前返回 `NULL`。

禁止使用“收入 × 固定利润率 × 25%”或地区名称自动套用 15% 优惠税率来补齐该指标。优惠资格和所得税分摊必须由已审核规则与主体资格事实驱动。
