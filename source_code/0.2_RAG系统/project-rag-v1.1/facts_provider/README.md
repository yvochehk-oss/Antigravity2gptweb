# Facts Provider

Facts Provider 是 AI Review 获取确定性结构化数据的唯一通道。

## 核心职责

1. **Canonical Facts API**：返回项目完整 Facts JSON
2. **Hybrid Freshness**：事件失效 + 按需刷新 + TTL 兜底
3. **Versioning**：每个响应包含 `as_of`、`metric_version`、`facts_version`
4. **Snapshot**：支持保存历史 Facts 用于 AI Review 追溯

## 架构

```
业务数据变更
    ↓
Domain Event
    ↓
FactsProvider.invalidate(project_id)
    ↓
Cache 失效

用户请求
    ↓
FactsProvider.get_facts(project_code, require_fresh, max_age, as_of)
    ↓
检查缓存 → 返回 or 重新计算 → 返回 + 缓存
```

## API 接口

### 获取 Facts

```http
GET /api/v1/facts/projects/{project_code}
GET /api/v1/facts/projects/{project_code}?max_age=300
GET /api/v1/facts/projects/{project_code}?require_fresh=true
GET /api/v1/facts/projects/{project_code}?as_of=2026-07-31
```

### 失效缓存

```http
POST /api/v1/facts/invalidate
{
  "project_code": "YB001"
}
```

## Freshness 策略

| 参数 | 含义 |
|------|------|
| `max_age` | 允许缓存最大秒数，默认由 `PROJECT_RAG_FACTS_CACHE_TTL_SECONDS`（当前默认 60）控制 |
| `require_fresh` | true 时强制重新获取 |
| `as_of` | 查询历史 Snapshot |

## 默认 TTL 与容量

- 普通查询：`PROJECT_RAG_FACTS_CACHE_TTL_SECONDS`（默认 60 秒）
- 进程内最大缓存条目：`PROJECT_RAG_FACTS_CACHE_MAX_ENTRIES`（默认 1024）
- 事件历史上限：`PROJECT_RAG_FACTS_CACHE_EVENT_LIMIT`（默认 1000）
- Dashboard：300-900 秒
- AI 深度体检：`require_fresh=true`
- 历史查询：`as_of=指定日期`
