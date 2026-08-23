# Analytics Contract - Dimensions

本目录包含建筑经营系统的维度定义。

## 目录结构

```
dimensions/
├── README.md
├── entity.yaml          # 法人主体维度
├── project.yaml         # 项目维度
├── time.yaml            # 时间维度
├── cost_category.yaml   # 成本分类维度
├── document_type.yaml   # 文档类型维度
└── _templates/
    └── dimension_template.yaml
```

## 维度定义原则

1. **可枚举性**：维度值应该是可枚举的（离散值）
2. **稳定性**：维度值不应频繁变更
3. **业务含义**：每个维度值必须有明确的业务含义
