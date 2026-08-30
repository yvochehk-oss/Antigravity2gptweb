# ProjectRAG V0.2 架构

## 服务边界

ProjectRAG 是独立知识服务，不共享建筑项目管理系统数据库。外部系统通过 `external_system + external_project_id + project_code` 建立映射。

## 摄取链

```text
Upload / Folder Scan
→ SHA-256
→ Document metadata
→ IngestJob
→ 内置 PDF/OCR/Office 解析器
→ content_list / Markdown
→ page-aware Chunk
→ BGE-M3
→ Chunk.embedding vector(1024)
→ PostgreSQL/pgvector
```

## 检索链

```text
Project + Metadata Filter
       ├─ Python Chinese BM25 candidates
       └─ pgvector cosine HNSW candidates
                 ↓
             RRF-style fusion
                 ↓
           Evidence Results
           Evidence Results
```

## 为什么 PostgreSQL + pgvector

项目、文档、版本、元数据、任务和查询日志本来就是强关系数据；把向量放在同一个 PostgreSQL 内可减少 V0.2 的运维复杂度，同时保留事务、过滤和 SQL 能力。

## Embedding

BGE-M3 dense vector dimension = 1024。V0.2 数据库列固定为 `vector(1024)`。

## 异步任务

V0.2 使用数据库 `ingest_jobs` + 单机 daemon worker。它满足 Mac 单机批量摄取的第一阶段需求。未来可以保持 Job API 不变，将执行器替换为 Redis/Celery/Dramatiq。

## 安全边界

- Folder Import 是本机管理功能，路径来自运行 ProjectRAG 的同一台机器。
- 原始资料不主动发送给外部 LLM。
- `/retrieve` 不调用生成模型，只返回检索证据。
- `/query` 只有显式配置回答模型后才会发送 rerank 后的证据。
