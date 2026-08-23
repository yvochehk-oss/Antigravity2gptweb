# Metabase Adapter

Metabase Adapter 负责将 Analytics Contract 的语义层配置同步到 Metabase。

## 设计原则

1. **单向数据流**：Analytics Contract (YAML) → Metabase Adapter → Metabase
2. **非双重定义**：Metabase 不作为 Source of Truth
3. **API 驱动**：使用 Metabase API 进行配置

## 功能

### P1 功能

- [ ] 同步字段显示名称
- [ ] 同步字段说明
- [ ] 配置字段语义类型（currency、percentage 等）
- [ ] 配置关系（joins）

### P2 功能

- [ ] 创建 Metabase Models
- [ ] 创建 Metabase Metrics
- [ ] 自动化同步调度

## 架构

```
analytics_contract/
     │
     ↓
metabase_adapter.py
     │
     ↓
Metabase API
     │
     ↓
Metabase Models / Metrics
```

## 使用方式

```bash
# 同步所有配置
python -m adapters.metabase.sync --all

# 同步特定指标
python -m adapters.metabase.sync --metric project_real_profit
```
