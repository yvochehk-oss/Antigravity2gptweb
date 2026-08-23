# Changelog

All notable changes to ProjectRAG are documented in this file.

## [1.1.0] - 2026-08-19

> 历史说明：本版本初始实现曾在 analytics view 不可用时返回离线占位值；当前源码已移除该行为，改为显式 `DEGRADED`。

### Added (合并项)

- **HTTP 桥接层** (`app/services/rag_http_client.py`)
  - 让 v1.0 时代的 ai_review / facts_provider 通过 httpx 调用 v0.2 风格的 RAG API
  - 容错：所有失败返回空结构（`[]` / `{"answer": ""}`），不阻塞上游流程
  - 单进程部署下零开销（直连 `http://127.0.0.1:921`），跨进程部署无需改代码

- **法规知识引擎接入**（来自 v0.2 base）
  - 模型：`Regulation` / `RegulationArticle` / `RegulationChunk` 追加到 `app/models.py`
  - Schema：`RegulationCreate` / `RegulationUpdate` / `RegulationArticleCreate` /
    `RegulationRetrieveRequest` / `RegulationQueryRequest`
  - 路由：`/api/v1/regulations/*` 9 个端点（list / create / get / patch / delete /
    list-articles / create-article / retrieve / query）
  - 服务：`app/services/regulation_retrieval.py`（BM25 + Embedding + RRF + Rerank）
  - 入库工具：`app/services/regulations_md_ingest.py`（路径改读 `REGULATIONS_DATA_DIR` 环境变量）

- **AI Review 路由** (`ai_review/routes.py`)
  - `POST /api/v1/ai-review/run`
  - `GET  /api/v1/ai-review/projects/{code}/history`
  - `GET  /api/v1/ai-review/health`

- **Facts 数据源接入（历史初始实现）**
  - `facts_provider._compute_facts` 优先查 `analytics_project_full` view
  - 历史行为：view 不存在或列不一致时曾 fallback 到占位值；该行为已移除
  - `routes.py` 用 `app.db.SessionLocal` 注入真实 db session

- **ai_review 表 DDL** (`sql/views/facts_provider_tables.sql`)
  - `facts_snapshots` / `ai_review_runs` / `metric_versions`
  - SQLAlchemy `create_all` 自动建表，DDL 仅作运维参考

- **V1.1 环境变量**
  - `RAG_HTTP_BASE_URL`（默认 `http://127.0.0.1:921`）
  - `RAG_HTTP_TIMEOUT`（默认 10 秒）
  - `REGULATIONS_DATA_DIR`（默认 `./regulations_data`）

### Changed

- `app/main.py` version: `0.2.1-optimized` → `1.1.0`
- `pyproject.toml` version: `1.0.0` → `1.1.0`，name: `project-rag-analytics` → `project-rag`
- 启动脚本端口: `8800` → `921`（沿用项目习惯端口 0921）
- 启动脚本: 新增 `setup_v11_mac.sh`，删除旧的 `setup_v02_mac.sh`

### Removed

- 无（v0.2 目录已在合并后删除；v0.2-optimized 源目录也已删除）

## [1.0.0] - 2026-08-18

### Added

- **Analytics Contract**：统一数据口径层（指标 / 维度 / 实体 / 关系）
- **SQL Views**：7 个 canonical views（`analytics_project_summary / _profit / _full / _eac / _cashflow / _tax / _cost`）
- **Facts Provider**：AI Review 数据通道（含 FactsCache 内存缓存）
- **AI Review**：整合 Facts + RAG + LLM 的核心服务（FactsSnapshot / AIReviewRun / MetricVersion 表）
- **Metabase 适配器**（P1 候选）
- **四层 Truth 模型**（L1 原始 / L2 确定性 / L3 分析 / L4 证据）

## [0.2.1-optimized] - 2026-08-16

### Fixed (P0)

- 数据库连接泄漏：统一使用 `get_db()` context manager
- 路径遍历安全：添加 `SAFE_ORIGIN_DIRS` 限制和 symlink 检测
- Worker 线程安全：添加 `get_worker_status()` 监控
- Session 管理：消除 `db.refresh()` 状态异常

### Added (P1)

- MinerU 指数退避重试（最多 3 次）
- 限流中间件（默认 60 req/min/IP）
- 实体管理 + Benchmark Runner + Knowledge Audit + Conflict Detector + Quality Gate
- v0.2 法规检索初版

## [0.2.0] - 2026-08-15

### Added

- 文档摄取链：Upload → SHA-256 → MinerU → Markdown → Chunk → BGE-M3 → pgvector
- 混合检索：BM25 + pgvector + RRF
- FastAPI + Jinja2 UI：dashboard / project / document / search
- Async Ingest Worker
- 法规知识引擎：BM25 + Embedding + RRF（条款级）
