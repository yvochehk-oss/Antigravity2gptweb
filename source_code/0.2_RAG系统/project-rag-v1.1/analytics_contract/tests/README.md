# Analytics Contract Tests

本目录包含 Analytics Contract 的所有测试用例。

## 目录结构

```
tests/
├── README.md
├── fixtures/                      # 测试数据和 fixtures
│   ├── test_data_project.yaml
│   └── expected_values.yaml
├── metric_tests/                  # 指标公式测试
│   ├── test_real_profit.py
│   ├── test_eac_margin.py
│   ├── test_collection_rate.py
│   └── test_cash_gap.py
├── integration_tests/             # 集成测试
│   ├── test_views.py
│   └── test_metrics_aggregation.py
└── reconciliation_tests/          # 数据一致性测试
    ├── test_project_detail_api.py
    └── test_ai_review_contract.py
```

## 测试原则

1. **公式测试**：验证指标计算公式正确
2. **Reconciliation**：验证不同消费者看到的数据一致
3. **边界测试**：验证边界条件和异常值处理

## 运行测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行特定测试
python -m pytest tests/metric_tests/ -v

# 运行 Reconciliation 测试
python -m pytest tests/reconciliation_tests/ -v
```
