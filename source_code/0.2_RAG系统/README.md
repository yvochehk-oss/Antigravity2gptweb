# ProjectRAG V0.2 Optimized

独立运行的项目资料知识库 / RAG 服务。V0.2 Optimized 是 V0.2 的生产级优化版本，包含所有原版功能及以下改进：

## 优化内容

### P0 - 关键修复

| 问题 | 优化 |
|------|------|
| 数据库连接泄漏 | 统一使用 `get_db()` context manager |
| 路径遍历安全 | 添加 `SAFE_ORIGIN_DIRS` 限制和 symlink 检测 |
| Worker 线程安全 | 添加 `get_worker_status()` 监控 |
| Session 管理 | 消除 `db.refresh()` 状态异常 |

### P1 - 功能增强

| 问题 | 优化 |
|------|------|
| 文档解析失败 | 由 Worker 指数退避重试 (最多3次) |
| Job 失败无重试 | 添加 `RETRY` 状态和 `next_retry_at` |
| API 无分页 | 所有列表 API 添加 `page`/`page_size` 参数 |
| BM25 全内存 | 支持 PostgreSQL FTS 索引加速 |

### P2 - 工程改进

| 问题 | 优化 |
|------|------|
| 无日志系统 | 添加 `logging_config.py` 结构化日志 |
| 文档删除不完整 | 添加 `cleanup_document_files()` |
| 错误处理宽泛 | 区分 `ModelLoadError` 等具体异常 |
| Embedding 无验证 | 添加向量维度验证 |

### P3 - 最佳实践

| 问题 | 优化 |
|------|------|
| 配置无验证 | 添加 `config_validator.py` |
| 连接池无配置 | 添加 `pool_size=10, max_overflow=20` |
| Reranker 无限制 | 限制输入 `MAX_RERANK_INPUT=100` |
| 缺少审计字段 | 添加 `created_at`, `updated_at`, `response_time_ms` |

## 新增 API

### 分页

```
GET /api/v1/projects/{id}?page=1&page_size=50
GET /api/v1/jobs?status=QUEUED&page=1&page_size=100
```

### 文档删除

```
DELETE /api/v1/documents/{id}
```

### 统计

```
GET /api/v1/stats?project_id=1
```

### 扩展健康检查

```json
GET /api/v1/health
{
  "status": "ok",
  "validation_errors": [],
  "worker": {
    "running": true,
    "thread_name": "projectrag-ingest-worker"
  }
}
```

## 新增环境变量

```bash
# 安全
PROJECT_RAG_MAX_UPLOAD_SIZE=104857600  # 100MB
PROJECT_RAG_RATE_LIMIT=60             # 每分钟请求数

# 重试
PROJECT_RAG_MAX_JOB_RETRIES=3
PROJECT_RAG_JOB_RETRY_BACKOFF=30

# BM25 优化
PROJECT_RAG_BM25_LIMIT=5000
PROJECT_RAG_BM25_POSTGRES_FTS=1

# 分页
PROJECT_RAG_DEFAULT_PAGE_SIZE=50
PROJECT_RAG_MAX_PAGE_SIZE=200

# 日志
PROJECT_RAG_LOG_LEVEL=INFO
```

## 架构改进

### 连接管理

```python
# 旧: 手动管理 (容易泄漏)
db = SessionLocal()
try:
    ...
finally:
    db.close()

# 新: Context manager (自动释放)
with get_db() as db:
    ...
```

### 任务重试

```
QUEUED → RUNNING → COMPLETED
                    ↓ (失败)
              attempts < max → RETRY (指数退避)
                    ↓ (达到上限)
                  FAILED
```

### PostgreSQL FTS 索引

```sql
CREATE INDEX ix_chunks_search_text_fts
ON chunks USING gin(to_tsvector('simple', search_text));
```

## 启动

```bash
# 1. 复制环境配置
cp .env.example .env

# 2. 一键安装
./setup_v02_mac.sh

# 3. 启动
./run.sh
```

## 版本信息

- **Version**: 0.2.1-optimized
- **基于**: V0.2
- **最低 Python**: 3.10
