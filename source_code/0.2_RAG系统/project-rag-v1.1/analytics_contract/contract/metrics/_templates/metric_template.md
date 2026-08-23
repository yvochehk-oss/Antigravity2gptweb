# 指标定义模板

## 基本信息

- metric_id: <metric_id>                    # 唯一标识，snake_case
- version: "<major.minor>"                  # 语义版本号

- name_cn: <中文名称>
- name_en: <English Name>

- owner: <负责部门>
- approved_by: <审批人>

- effective_from: "<YYYY-MM-DD>"            # 生效日期
- effective_to: null                        # 失效日期，null表示永久有效
- deprecates: "<previous_version>"           # 废弃的前一版本

## 定义

<用简洁的语言描述这个指标是什么>

## 计算公式

<主公式，用自然语言描述>

### <可选：子公式或辅助计算>

```sql
<SQL表达式>
```

## 数据粒度

- grain: <project / project+entity / ...>
- 时间维度: <实时/月度/季度/年度>

## 数据来源

- source_view: `<对应的analytics view名称>`
- source_columns:
  - `<column_a>`: <说明>
  - `<column_b>`: <说明>

## 验证规则

- <规则1描述>
- <规则2描述>

## 版本变更历史

### v<version> (<date>)
- **change_reason**: <变更原因>
- **formula_engine**: <对应的计算引擎版本>

### v<previous_version> (<date>) [已废弃]
- <上一个版本的摘要>

## 使用场景

- <场景1>
- <场景2>

## 依赖指标

- <依赖的metric_id>：<说明>
- 无

## 示例数据

```json
{
  "project_code": "YB001",
  "<metric_id>": <示例值>,
  "as_of": "2026-08-01T00:00:00+08:00"
}
```
