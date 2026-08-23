# Analytics Contract - Metrics

本目录包含建筑经营系统的所有正式指标定义。每个指标都是团队计算重要数字的官方方式。

## 目录结构

```
metrics/
├── README.md                          # 本文件
├── project_real_profit.yaml           # 项目综合真实利润
├── project_eac_profit.yaml            # 项目EAC预计利润
├── project_eac_margin.yaml            # 项目EAC利润率
├── collection_rate.yaml               # 回款率
├── tax_burden_rate.yaml               # 税负率
├── cash_gap_30d.yaml                  # 30天现金缺口
├── cost_variance.yaml                 # 成本偏差率
├── internal_elimination.yaml          # 内部交易抵消
└── _templates/
    └── metric_template.yaml           # 指标定义模板
```

## 核心指标列表

| metric_id | name_cn | 版本 | 状态 |
|-----------|---------|------|------|
| project_real_profit | 项目综合真实利润 | 2.1 | 有效 |
| project_eac_profit | 项目EAC预计利润 | 1.6 | 有效 |
| project_eac_margin | 项目EAC利润率 | 1.6 | 有效 |
| collection_rate | 回款率 | 1.2 | 有效 |
| tax_burden_rate | 税负率 | 1.0 | 有效 |
| cash_gap_30d | 30天现金缺口 | 1.0 | 有效 |
| cost_variance | 成本偏差率 | 1.0 | 有效 |

## 指标定义原则

1. **唯一口径**：每个指标只能有一个官方定义
2. **版本管理**：指标变更必须新增版本号
3. **可追溯**：历史查询使用对应版本的定义
4. **可测试**：每个指标必须有对应的测试用例

## 添加新指标

参考 `_templates/metric_template.yaml` 创建新的指标定义文件。
