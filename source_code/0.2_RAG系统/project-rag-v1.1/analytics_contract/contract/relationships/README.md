# Analytics Contract - Relationships

本目录包含实体之间的关系定义。

## 目录结构

```
relationships/
├── README.md
├── project_invoice.yaml
├── project_payment.yaml
├── project_collection.yaml
├── project_contract.yaml
├── internal_transaction.yaml
└── _templates/
    └── relationship_template.yaml
```

## 关系定义原则

1. **明确基数**：一对多、多对多等关系明确
2. **可追溯**：关系路径清晰，可追溯数据来源
3. **与 Views 对齐**：关系定义与 SQL Views 的 JOIN 逻辑一致
