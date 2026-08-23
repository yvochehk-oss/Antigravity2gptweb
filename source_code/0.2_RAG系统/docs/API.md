# ProjectRAG V0.2 API

核心接口：

- `GET /api/v1/health`
- `GET /api/v1/projects`
- `POST /api/v1/projects`
- `POST /api/v1/projects/sync`
- `GET /api/v1/projects/{id}`
- `GET /api/v1/projects/{id}/audit`
- `POST /api/v1/documents/upload`
- `POST /api/v1/documents/import-folder`
- `GET /api/v1/documents/{id}`
- `PATCH /api/v1/documents/{id}/metadata`
- `POST /api/v1/documents/{id}/parse`
- `GET /api/v1/documents/{id}/original`
- `GET /api/v1/jobs/{id}`
- `POST /api/v1/jobs/process-next`
- `POST /api/v1/retrieve`
- `POST /api/v1/query`

## retrieve

`rerank=true` 默认启用 BGE reranker；在模型不可用时自动返回融合排序结果。

返回中 `page_start/page_end/heading_path` 来自 MinerU结构化结果或Markdown标题解析，可供上层系统形成证据引用。
