# ProjectRAG V1.1

> **数据库架构**：V2 收敛版为 PostgreSQL-only。Tax 与 RAG 共用同一个 `projectrag` 数据库；Tax migrations 先执行，RAG migrations 后执行。SQLite 文件仅属于历史版本，不参与当前运行、同步或测试主路径。


基于四轮架构讨论收敛的建筑经营数据分析平台（合并自 v0.2-optimized + v1.0 + v0.2 regulation retrieval）。

## 核心架构

V1.1 在三个前代基础上收敛：

1. **v0.2-optimized**：MinerU 文档摄取、BM25+pgvector+RRF 混合检索、Worker 异步解析
2. **v1.0**：Analytics Contract / Facts Provider / AI Review / 四层 Truth 模型
3. **v0.2 base**：法规知识引擎（BM25 + Embedding + RRF + 条款级读取）

V1.1 通过 HTTP 桥接层（`app/services/rag_http_client.py`）让 v1.0 的 ai_review / facts_provider
直接消费 v0.2 的 RAG 证据，单进程部署下 httpx 直连本进程零开销。

## 目录结构

```
project-rag-v1.1/
├── app/                                # 核心 RAG 服务（来自 v0.2-optimized）
│   ├── main.py                         # FastAPI 入口，整合全部路由
│   ├── models.py                       # ORM 模型（合并 + Regulation/Article/Chunk）
│   ├── schemas.py                      # Pydantic 模型
│   ├── services/
│   │   ├── retrieval.py                # 文档混合检索（BM25 + pgvector + RRF）
│   │   ├── regulation_retrieval.py     # 法规混合检索
│   │   ├── rag_http_client.py          # HTTP 桥接层（V1.1 新增）
│   │   ├── embeddings.py / reranker.py # bge-m3 / bge-reranker-v2-m3
│   │   ├── mineru_adapter.py           # MinerU PDF 解析
│   │   ├── llm.py                      # OpenAI-compatible LLM 调用
│   │   ├── jobs.py                     # 异步 Ingest Worker
│   │   ├── extractor.py                # LLM 结构化抽取
│   │   ├── tax_extraction.py           # 税务抽取
│   │   └── ...                         # 其余 11 个服务
│   └── templates/                      # Jinja2 UI（dashboard/project/document/search）
├── ai_review/                          # AI 体检服务（来自 v1.0）
│   ├── rag_client.py                   # RAG 客户端封装（V1.1 新增）
│   ├── review_service.py               # run_review 核心
│   ├── snapshot_service.py             # Facts 快照
│   ├── run_service.py                  # 运行记录
│   └── models.py                       # FactsSnapshot / AIReviewRun / MetricVersion
├── facts_provider/                     # Facts Provider（来自 v1.0）
│   ├── facts_provider.py               # Facts 计算（V1.1 接入 analytics view）
│   └── routes.py                       # /api/v1/facts/* 端点
├── analytics_contract/                 # 数据口径（来自 v1.0）
│   ├── contract/{metrics,dimensions,entities,relationships}/
│   └── tests/                          # 4 个测试套件
├── adapters/metabase/                  # Metabase 适配器（来自 v1.0）
├── regulations_data/                   # 法规语料仓库（来自 v1.0）
├── sql/views/                          # Canonical SQL Views
│   ├── analytics_*.sql                 # v1.0 analytics views
│   └── facts_provider_tables.sql       # V1.1 新增：snapshot/run 表 DDL
├── docs/                               # 完整文档
├── examples/                           # construction_rag_client.py + 示例合同
├── scripts/                            # 实体抽取 / benchmark 初始化脚本
├── data/                               # 运行时数据（cache/originals/parsed）
├── tests/                              # Smoke + 桥接测试
├── pyproject.toml                      # name=project-rag, version=1.1.0
├── requirements*.txt                   # 依赖（已合并）
├── .env.example                        # 环境变量（含 RAG_HTTP_BASE_URL）
├── setup_v11_mac.sh                    # Mac 一键安装
├── run.sh / run_mac.command            # 启动入口
└── install_*.sh                        # 子安装脚本
```

## 四层 Truth 模型

```
┌─────────────────────────────────────────────┐
│ L1 - Transactional Truth                    │
│ 原始业务记录（合同/发票/付款）              │
├─────────────────────────────────────────────┤
│ L2 - Deterministic Truth                    │
│ 规则计算（税额/EAC/真实成本）               │
├─────────────────────────────────────────────┤
│ L3 - Analytical Truth                       │
│ Analytics Contract 统一指标                  │
├─────────────────────────────────────────────┤
│ L4 - Documentary Evidence                  │
│ ProjectRAG 原文证据（V1.1 新增法规证据）    │
└─────────────────────────────────────────────┘

AI Review = L2 + L3 + L4（做 Reasoning，不做 Calculation）
```

## 核心模块与端点

### RAG 服务（v0.2 风格，前缀 `/api/v1`）

| 端点 | 用途 |
|---|---|
| `GET  /api/v1/health` | 健康检查（含 DB / Embedding / Reranker / Worker 状态） |
| `POST /api/v1/projects` | 创建项目 |
| `POST /api/v1/projects/sync` | 按 external_system + external_project_id 同步项目 |
| `POST /api/v1/documents/upload` | 上传文档（multipart，自动 MinerU 解析） |
| `POST /api/v1/documents/import-folder` | 批量导入文件夹 |
| `POST /api/v1/retrieve` | 文档混合检索（无 LLM） |
| `POST /api/v1/query` | 文档问答（检索 + LLM 回答） |
| `POST /api/v1/regulations/retrieve` | 法规检索（BM25 + Embedding + RRF） |
| `POST /api/v1/regulations/query` | 法规问答（条款级展开 + LLM） |
| `POST /api/v1/regulations` | 录入法规 |

### Facts Provider（前缀 `/api/v1/facts`，来自 v1.0）

| 端点 | 用途 |
|---|---|
| `GET  /api/v1/facts/projects/{project_code}` | 获取项目 Facts |
| `POST /api/v1/facts/invalidate` | 失效缓存 |
| `GET  /api/v1/facts/projects/{project_code}/history` | 历史 Snapshot |

### AI Review（前缀 `/api/v1/ai-review`，来自 v1.0）

| 端点 | 用途 |
|---|---|
| `POST /api/v1/ai-review/run` | 触发项目 AI 体检 |
| `GET  /api/v1/ai-review/projects/{project_code}/history` | Review 历史 |

## 安装与启动（macOS）

```bash
./setup_v11_mac.sh           # 安装 PostgreSQL + AI 模型
./install_mineru_mac.sh      # （可选）MinerU PDF 解析
./run.sh                     # 启动 FastAPI（默认端口 8922，可由 PROJECT_RAG_PORT 覆盖）
```

UI 入口：`http://127.0.0.1:8922`
OpenAPI 文档：`http://127.0.0.1:8922/docs`

启动脚本 `run_mac.command` 与 `run.sh` 使用同一组 `PROJECT_RAG_HOST` /
`PROJECT_RAG_PORT` 环境变量；默认均为 `127.0.0.1:8922`。如需改端口，先在
运行环境或 `.env` 中设置 `PROJECT_RAG_PORT`，双击启动器会读取该配置。

## Tax ↔ RAG 认证环境注入

Tax 和 RAG 是两个独立进程，必须分别从各自的运行环境/secret manager 注入
同名变量 `RAG_SHARED_API_KEY`，并确保两边的值一致。不要复制一端的 `.env`，
也不要以 Tax 的 `TAX_RAG_API_KEY` 作为共享密钥或 fallback；该变量如因旧端点
兼容保留，也只能表示独立的端点级凭据。生产、预发布、非回环绑定、自动同步
或显式认证模式下缺少 `RAG_SHARED_API_KEY` 应 fail fast。

## V1.1 关键变更

| 项 | 说明 |
|---|---|
| HTTP 桥接层 | `app/services/rag_http_client.py`，ai_review/facts_provider 通过 httpx 调用 RAG |
| Regulation 模型 | 从 v0.2 base 移植到 `app/models.py`，含 Regulation/RegulationArticle/RegulationChunk |
| 法规端点 | `/api/v1/regulations/*` 9 个端点（CRUD + retrieve + query） |
| Facts 数据源 | `facts_provider._compute_facts` 只读取 `analytics_project_full`；缺失或不完整时显式返回 `DEGRADED`，不生成占位财务数值 |
| 表 DDL | `sql/views/facts_provider_tables.sql`（facts_snapshots / ai_review_runs / metric_versions） |
| pyproject | name=project-rag, version=1.1.0，加入 app 包 |
| 启动脚本 | 默认监听端口统一为 8922；新增 setup_v11_mac.sh；删除 setup_v02_mac.sh |
| 法规入库 | `python -m app.services.regulations_md_ingest --dry` 解析 regulations_data/*.md |

## 测试

```bash
pytest analytics_contract/tests -v   # 指标 / 集成 / 对账 测试
pytest tests/ -v                     # smoke + 桥接
```

## 文档

- [架构文档](docs/ARCHITECTURE.md)
- [API 参考](docs/API.md)
- [安装说明](docs/INSTALL_MAC_V02.md)（MinerU 见 INSTALL_MINERU_MAC.md）
- [MinerU 兼容性](docs/MINERU_COMPATIBILITY.md)
- [对接建筑项目管理系统](docs/INTEGRATION_CONSTRUCTION_SYSTEM.md)
- [v0.2 优化报告](docs/optimization/OPTIMIZATION_REPORT.md)
- [变更日志](CHANGELOG.md)

## 版本历史

| 版本 | 日期 | 说明 |
|---|---|---|
| 1.1.0 | 2026-08-19 | 合并 v0.2-optimized + v1.0 + v0.2 regulation |
| 1.0.0 | 2026-08-18 | 初始 v1.0（Analytics Contract + Facts Provider + AI Review） |
| 0.2.1-optimized | 2026-08-16 | v0.2 优化版（DB 连接 / 路径安全 / Worker 监控 / MinerU 重试） |
| 0.2.0 | 2026-08-15 | v0.2 base（RAG 服务 + 法规检索初版） |


## PostgreSQL-only 架构

当前正式架构仅支持 PostgreSQL；数据库结构由 Alembic 管理。详见 `docs/POSTGRESQL_ONLY_ARCHITECTURE.md`。
