# ProjectRAG V1.1 API

V1.1 单进程 FastAPI 提供三组端点（前缀分别为 `/api/v1` / `/api/v1/facts` / `/api/v1/ai-review`）。

## 1. RAG 服务（v0.2 风格）

### Projects
- `GET  /api/v1/projects`
- `POST /api/v1/projects`
- `POST /api/v1/projects/sync`
- `GET  /api/v1/projects/{id}`

### Documents
- `POST /api/v1/documents/upload` （multipart）
- `POST /api/v1/documents/import-folder`
- `GET  /api/v1/documents/{id}`
- `PATCH /api/v1/documents/{id}/metadata`
- `POST /api/v1/documents/{id}/parse`

### Jobs / Health
- `GET  /api/v1/jobs/{id}`
- `POST /api/v1/jobs/process-next`
- `GET  /api/v1/health`

### Retrieval / QA
- `POST /api/v1/retrieve` — 文档混合检索（无 LLM）
- `POST /api/v1/query` — 文档问答（检索 + LLM）

`rerank=true` 默认启用 BGE reranker；模型不可用时自动返回融合排序结果。
返回中 `page_start / page_end / heading_path` 来自 MinerU 结构化结果或 Markdown 标题解析。

### Regulations（V1.1 新增）
- `GET  /api/v1/regulations`
- `POST /api/v1/regulations`
- `GET  /api/v1/regulations/{id}`
- `PATCH /api/v1/regulations/{id}`
- `DELETE /api/v1/regulations/{id}`
- `GET  /api/v1/regulations/{id}/articles`
- `POST /api/v1/regulations/{id}/articles`
- `POST /api/v1/regulations/retrieve` — 法规混合检索（BM25 + Embedding + RRF + 可选 Rerank）
- `POST /api/v1/regulations/query` — 法规问答（条款级展开 + LLM）

## 2. Facts Provider（v1.0）

- `GET  /api/v1/facts/projects/{project_code}` — 获取 Facts
- `POST /api/v1/facts/invalidate` — 失效缓存
- `GET  /api/v1/facts/projects/{project_code}/history` — 历史 Snapshot

Facts 仅从 `analytics_project_full` view 或已保存的 `facts_snapshots` 读取。
数据源缺失、项目无行或指标不完整时返回 `status=DEGRADED`、
`facts_available=false` 和空指标；系统不会返回占位财务数值。

## 3. AI Review（v1.0）

- `POST /api/v1/ai-review/run`
- `GET  /api/v1/ai-review/projects/{project_code}/history`
- `GET  /api/v1/ai-review/health`

## 完整 OpenAPI 文档

启动后访问 `http://127.0.0.1:8922/docs`。
