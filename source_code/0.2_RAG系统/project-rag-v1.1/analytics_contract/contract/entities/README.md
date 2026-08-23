# Analytics Contract - Entities

本目录包含数据分析中使用的实体定义（与业务数据库表对应）。

## 目录结构

```
entities/
├── README.md
├── project.yaml
├── invoice.yaml
├── payment.yaml
├── collection.yaml
├── contract.yaml
├── cost_record.yaml
└── _templates/
    └── entity_template.yaml
```

## 实体定义原则

1. **数据来源明确**：每个实体对应具体的业务表或视图
2. **字段语义清晰**：每个字段有明确的业务含义
3. **与指标关联**：实体与 metrics 的关系明确
