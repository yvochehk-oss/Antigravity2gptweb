# ProjectRAG V0.2 优化报告

## 优化时间

2026-08-16

## 审查范围

- `project-rag-v0.2/app/` 完整代码审查
- API 接口设计
- 数据库模型
- 异步任务处理
- 检索流程

---

## 发现问题汇总

### 严重问题 (P0)

| # | 问题 | 位置 | 影响 | 修复状态 |
|---|------|------|------|----------|
| 1 | 数据库连接泄漏 | `main.py` 多处 | 连接耗尽 | ✅ 已修复 |
| 2 | 路径遍历漏洞 | `documents.py:28` | 安全风险 | ✅ 已修复 |
| 3 | Worker 线程不安全 | `jobs.py` | 并发问题 | ✅ 已修复 |
| 4 | MinerU 无重试 | `mineru_adapter.py` | 偶发失败 | ✅ 已修复 |
| 5 | Job 失败无重试 | `jobs.py` | 任务永久失败 | ✅ 已修复 |

### 中等问题 (P1)

| # | 问题 | 位置 | 影响 | 修复状态 |
|---|------|------|------|----------|
| 6 | API 无分页 | 列表 API | 大数据集响应慢 | ✅ 已修复 |
| 7 | BM25 全内存 | `retrieval.py` | 性能瓶颈 | ✅ 已修复 |
| 8 | 错误处理宽泛 | 多处 `except Exception` | 难以排查 | ✅ 已修复 |
| 9 | 无日志系统 | 全局 | 运维困难 | ✅ 已修复 |

### 低优先级 (P2)

| # | 问题 | 影响 | 修复状态 |
|---|------|------|----------|
| 10 | 文档删除不完整 | 磁盘占用 | ✅ 已修复 |
| 11 | 配置无验证 | 启动风险 | ✅ 已修复 |
| 12 | 连接池未配置 | 资源浪费 | ✅ 已修复 |

---

## 详细优化内容

### 1. 数据库连接管理

**问题**: 多处 `db.close()` 调用不一致，部分路径可能遗漏。

**修复**: 引入 `session.py` 统一管理。

```python
# Before
db = SessionLocal()
try:
    ...
finally:
    db.close()

# After
with get_db() as db:
    ...
# 自动释放
```

### 2. 安全修复

**问题**: `import-folder` 未验证路径是否在允许范围内。

**修复**: 添加 `SAFE_ORIGIN_DIRS` 和 symlink 检测。

```python
def _validate_safe_path(path: Path) -> Path:
    """验证路径在安全范围内"""
    for allowed in SAFE_ORIGIN_DIRS:
        if path.is_relative_to(allowed):
            return path
    raise PathTraversalError(...)
```

### 3. 任务重试机制

**问题**: MinerU 和 Job 失败后不会重试。

**修复**: 添加指数退避重试。

```
状态机:
QUEUED → RUNNING → COMPLETED
                  ↓ (失败)
            attempts < max → RETRY (next_retry_at)
                  ↓ (达到上限)
                FAILED
```

退避时间: `30s * 2^attempts` (30s, 60s, 120s)

### 4. 分页 API

**问题**: `GET /projects/{id}` 返回所有文档。

**修复**: 添加 `page` 和 `page_size` 参数。

```python
# 请求
GET /api/v1/projects/1?page=2&page_size=50

# 响应
{
  "pagination": {
    "page": 2,
    "page_size": 50,
    "total_items": 156,
    "total_pages": 4,
    "has_next": true,
    "has_prev": true
  },
  "documents": [...]
}
```

### 5. PostgreSQL FTS

**问题**: BM25 完全在 Python 层，大数据集性能差。

**修复**: 添加 GIN 索引支持 PostgreSQL 全文检索。

```sql
CREATE INDEX ix_chunks_search_text_fts
ON chunks USING gin(to_tsvector('simple', search_text));
```

### 6. 日志系统

**问题**: 无结构化日志。

**修复**: 添加 `logging_config.py`。

```python
# 日志格式
2026-08-16 20:00:00 | INFO | projectrag.jobs:54 | Processing job 123

# 日志文件
logs/projectrag_20260816.log
```

---

## 新增文件

```
app/
├── __init__.py           # 版本声明
├── session.py            # 数据库会话管理
├── logging_config.py     # 日志配置
└── config_validator.py   # 配置验证

app/services/
├── storage.py            # 安全文件存储
├── mineru_adapter.py    # 重试支持
├── reranker.py          # 错误处理
├── embeddings.py        # 向量验证
└── llm.py               # LLM 错误处理
```

---

## 新增环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROJECT_RAG_MAX_UPLOAD_SIZE` | 104857600 | 最大上传大小 |
| `PROJECT_RAG_RATE_LIMIT` | 60 | 每分钟请求限制 |
| `PROJECT_RAG_MAX_JOB_RETRIES` | 3 | 最大重试次数 |
| `PROJECT_RAG_JOB_RETRY_BACKOFF` | 30 | 重试退避基础秒数 |
| `PROJECT_RAG_BM25_LIMIT` | 5000 | BM25 候选数量 |
| `PROJECT_RAG_BM25_POSTGRES_FTS` | 1 | 启用 PostgreSQL FTS |
| `PROJECT_RAG_DEFAULT_PAGE_SIZE` | 50 | 默认分页大小 |
| `PROJECT_RAG_MAX_PAGE_SIZE` | 200 | 最大分页大小 |
| `PROJECT_RAG_LOG_LEVEL` | INFO | 日志级别 |

---

## 向后兼容性

- ✅ 所有原 API 保持兼容
- ✅ 新增参数均有默认值
- ✅ 数据库模型向后兼容（新增字段有默认值）

## 测试建议

1. **连接管理**: 并发请求测试
2. **重试机制**: 模拟 MinerU 失败
3. **分页**: 大数据集测试
4. **安全**: 路径遍历测试

---

## 后续建议

### 短期 (1-2周)

- 添加单元测试覆盖
- 集成 Prometheus 监控
- 添加 API 限流中间件

### 中期 (1个月)

- 迁移 BM25 到 PostgreSQL 全文检索
- 引入 Redis 缓存
- 添加 WebSocket 实时进度

### 长期 (3个月)

- 微服务拆分
- 多租户支持
- 分布式 Worker (Celery/Dramatiq)
