# 成都建工 AI 财税智控与 RAG V2.0
# AI Agent 运行、维护与验证说明

> 文档类型：供 Codex/Luna/审计 Agent 使用的运行维护基线，不是终端用户手册。
> 工作区：`/Users/yvoche/AI开发/073_成都建工/V2.0`
> 本文只记录当前源码和可复核运行状态；不把本文当作最终 GO/NO-GO 验收报告。

## 0. Agent 读取协议

每次 Agent 执行前，按以下顺序确认事实：

1. 读取仓库根目录 `AGENTS.md` 和本文件。
2. 以当前工作区源码、当前进程、`projectrag` 实时 schema/数据和实际测试结果为准；旧报告、旧聊天记录只能作为待复核线索。
3. 修改前确认文件所有权、迁移依赖和数据库目标。只做父任务明确授权的窄修复。
4. 涉及正式数据库写入时，先备份，再在独立 disposable PostgreSQL 数据库验证，再执行正式迁移/修复。
5. 输出必须区分：已验证、失败、未运行、环境受限、范围外。不能把 skipped 当作通过。
6. 任何财务金额、税额、EAC、利润、四流结果都必须来自确定性计算或已落库的 Canonical Facts；LLM 只能解释、审查、引用证据和提出建议。

除本文件明确授权外，不得访问、连接、迁移、清理或删除 `projectrag_xianyu`。

## 1. 当前 V2.0 事实快照

以下状态是在 2026-08-24 对当前工作区和本机 `projectrag` 做只读核对得到的；重新启动、迁移或数据修复后必须重新查询。

| 项目 | 当前事实 |
|---|---|
| 正式数据库 | PostgreSQL 数据库 `projectrag`，本机 `127.0.0.1:5432` |
| Tax migration head | `62_ai_review_batch_status` |
| RAG migration head | `012_document_storage_paths`（其上游为 `011_entity_mapping_facts_gate`） |
| entities | 26 行 |
| 合法独立法人 | 25 家；A04 为非独立法人 |
| A04 | `legal_entity=false`，`parent_entity_code=A03`，确定性税务口径归集到 A03 |
| projects | 6 行 |
| 当前项目主体映射 | 6 个项目的 `entity_code` 均为 `NULL`，必须人工确认后再映射 |
| tax_ledgers | 25 行 |
| facts_snapshots | 0 行（当前没有已持久化 Facts Snapshot） |
| Git | 当前工作区存在大量已修改/未跟踪文件；本指南编写时没有提交 Git |

当前 6 个项目代码为：`YB-DEMO-001`、`CD-TF-001`、`CY-CQ-002`、`GY-LZ-003`、`CD-GX-004`、`QY-GEM-005`。未完成主体映射前，不得通过项目名称、文件夹名、金额或历史规则猜测 `entity_code`，也不得为满足页面展示而删除项目。

只读核对命令（不会写入数据库）：

```bash
cd "/Users/yvoche/AI开发/073_成都建工/V2.0"
psql "postgresql://<db-user>@127.0.0.1:5432/projectrag" -X -P pager=off <<'SQL'
SELECT 'tax=' || version_num FROM alembic_version_tax;
SELECT 'rag=' || version_num FROM alembic_version_rag;
SELECT 'entities=' || count(*) FROM entities;
SELECT 'projects=' || count(*) FROM projects;
SELECT 'unmapped_projects=' || count(*) FROM projects WHERE entity_code IS NULL;
SELECT 'tax_ledgers=' || count(*) FROM tax_ledgers;
SELECT 'facts_snapshots=' || count(*) FROM facts_snapshots;
SELECT code, business_role, legal_entity, parent_entity_code
FROM entities ORDER BY code;
SELECT id, project_code, entity_code FROM projects ORDER BY id;
SQL
```

本快照不是最终验收结论。尤其是 6 个项目主体映射、完整 Analytics Facts 生成、外部模型可用性和真实端到端结果，必须按本文件的门禁重新验证。

## 2. 架构边界

系统保持以下单向数据边界：

```text
Structured Truth / Documentary Truth
              +
Deterministic Calculation
              |
              v
        analytics_*
              |
              v
      Canonical Facts
              |
              v
          AI Review
```

### 2.1 各层职责

- Structured Truth：Tax PostgreSQL 中的项目、主体、合同、发票、付款、税务台账等结构化业务记录。
- Documentary Truth：RAG 中上传的合同、发票、完税凭证、法规和解析后的文档/Chunk；它提供原文证据，不替代结构化金额。
- Deterministic Calculation：税额、项目经营指标、EAC、真实成本、四流匹配、内部交易抵消等由代码规则计算，不能由 LLM 代算。
- `analytics_*`：Analytics Contract 定义和数据库视图，是 Facts Provider 的统一分析来源。
- Canonical Facts：对项目和主体口径校验后的可消费事实。视图缺失、项目没有合法主体映射、指标不完整或数据源不可用时必须 fail-closed。
- AI Review：读取 Facts 和 RAG 证据，输出解释、风险、引用、建议和数据缺口；不得覆盖 Facts 或确定性结果。

### 2.2 不允许的越界

- RAG 文本抽取结果不能直接覆盖 Tax 的确定性计算结果。
- 页面不能把 `DEGRADED`/`UNAVAILABLE` 改写为 0、演示金额或“正常”。
- AI 输出中的金额必须能回溯到 Facts/结构化记录；没有 Facts 时只能报告缺口。
- `A`、`B`、`C`、`D` 只能是 `business_role` 聚合标签，不能当作法人代码或默认付款方。

## 3. 目录与唯一运行入口

```text
V2.0/
├── start_all.sh                         # Tax + RAG 一键启动
├── stop_all.sh                          # 只停止 Tax/RAG，不停止 PostgreSQL
├── start_postgres.sh / stop_postgres.sh # 本机 PostgreSQL 进程管理
├── database/backups/                    # pg_dump 自定义格式备份及校验文件
├── models/
│   ├── bge-m3/                          # 1024 维 Embedding 模型
│   └── bge-reranker-v2-m3/              # Reranker 模型
├── project_materials/                   # V2 允许读取的工程凭证目录
└── source_code/
    ├── 0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/
    │   └── chengdu_construction_tax_system_v1_0/ # Tax FastAPI + React
    ├── 0.2_RAG系统/project-rag-v1.1/    # ProjectRAG/Facts/AI Review
    └── 0.3_老板端安卓App_天府掌舵/       # Capacitor/Vue 移动端
```

V2.0 全量启动时 `start_all.sh` 是权威入口。旧的 `run.sh`、`run_demo.sh`、`make demo` 可能包含历史行为，不能拿来启动正式 `projectrag`。RAG 的 `tests/_archive_sqlite` 和 Tax 的 `tests/_archive_sqlite` 只是历史资料，不属于当前 PostgreSQL 主路径。

## 4. 运行前置条件

- macOS 本地 PostgreSQL，启动脚本默认寻找 `/opt/homebrew/opt/postgresql@18/bin/pg_ctl`，端口为 `5432`。
- PostgreSQL 必须启用 `pgvector`；RAG 的向量列固定为 `vector(1024)`，模型维度不能任意改动。
- 已安装 `uv`，Tax/RAG 各自维护 `.venv`；`start_all.sh` 会分别执行 `uv sync` 和 `uv sync --inexact`。
- Tax Python 要求见 Tax `pyproject.toml`（`>=3.10`）；Boss App `package.json` 要求 Node `>=22.12.0`。
- `curl`、`lsof` 或 `nc`、`pg_isready` 用于启动脚本的健康检查。
- RAG 默认强制 HuggingFace/Transformers 离线加载本地模型（`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`）；启动前必须确保模型文件可读。

## 5. 环境变量与密钥边界

### 5.1 注入原则

Tax 和 RAG 是两个独立进程。不要把一端 `.env` 原样复制给另一端；分别从进程环境、受限权限的本地 `.env` 或 secret manager 注入配置。真实密钥不写入本指南、源码、Git、日志、数据库记录或测试输出。

```bash
cd "/Users/yvoche/AI开发/073_成都建工/V2.0"
cp "source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/.env.example" \
   "source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/.env"
cp "source_code/0.2_RAG系统/project-rag-v1.1/.env.example" \
   "source_code/0.2_RAG系统/project-rag-v1.1/.env"
chmod 600 \
  "source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/.env" \
  "source_code/0.2_RAG系统/project-rag-v1.1/.env"
```

上述命令只建立本机配置文件；填写值时使用团队 secret manager 分发的真实值，禁止在终端回显或把命令历史作为交付物。

### 5.2 通用变量

| 变量 | 作用与约束 |
|---|---|
| `APP_ENV` | `development`、`test` 或受保护的 `production`/`staging` 等；受保护环境缺少必要密钥时 fail fast |
| `DATABASE_URL` | Tax PostgreSQL URL；拒绝 SQLite |
| `PROJECT_RAG_DB_URL` | RAG PostgreSQL URL；必须指向 `projectrag` 运行库，测试时必须显式 disposable URL |
| `JWT_SECRET_KEY` | Tax 签发、RAG 校验的同一 HS256 JWT secret；启动脚本会校验两端配置一致且至少 32 字符 |
| `RAG_SHARED_API_KEY` | Tax↔RAG 服务间 bearer key；它与 JWT 是两种不同凭据，禁止互相 fallback |
| `TAX_PORT` / `PROJECT_RAG_PORT` | 默认 `8921` / `8922` |
| `PROJECT_RAG_HOST` | 默认 `127.0.0.1`；非回环绑定会触发 RAG 认证和 HTTPS/CSRF 约束 |
| `STARTUP_TIMEOUT_SECONDS` | `start_all.sh` 等待服务健康的上限，默认 180 秒 |

示例只能写占位符，不得写真实值：

```dotenv
DATABASE_URL=postgresql+psycopg://<user>:<password>@127.0.0.1:5432/projectrag
PROJECT_RAG_DB_URL=postgresql+psycopg://<user>:<password>@127.0.0.1:5432/projectrag
JWT_SECRET_KEY=<same-long-random-value-in-both-processes>
RAG_SHARED_API_KEY=<same-long-random-service-key-in-both-processes>
```

### 5.3 Tax 关键变量

| 变量 | 运行规则 |
|---|---|
| `TAX_RAG_SERVICE_URL` | Tax 调用 RAG 的服务地址，默认 `http://127.0.0.1:8922`；回环开发地址可用，其他地址按 RAG URL 校验规则处理 |
| `TAX_RAG_V1_FACTS_URL` | Tax 读取 RAG Facts 的地址，默认与 RAG 服务地址相同 |
| `TAX_AUTO_SYNC_ENABLED` | 自动同步开关；启用时必须同时配置 `RAG_SHARED_API_KEY` |
| `TAX_RAG_AUTH_REQUIRED` | 显式要求 Tax→RAG 认证；启用时缺 key 启动失败 |
| `TAX_RAG_API_KEY` | 旧兼容变量；不能作为 `RAG_SHARED_API_KEY` 的 fallback |
| `TAX_RAG_V1_FACTS_API_KEY` | Facts 专用客户端 key；未配置时代码默认使用共享 key |
| `TAX_FACTS_MAX_AGE` / `TAX_FACTS_REQUIRE_FRESH` | Tax Facts 缓存新鲜度和 AI 审查新鲜度策略 |
| `AI_ALLOW_PRIVATE_LLM` | Tax 内部部署允许回环/RFC1918 LLM 使用 HTTP/HTTPS；当前内部部署可设 `1` |
| `AI_ALLOWED_HOSTS` | 公网 AI host 白名单；公网端点仍必须 HTTPS |
| `TAX_AI_API_KEY`、`OPENAI_API_KEY`、`OPENROUTER_API_KEY` | 仅通过批准的环境变量名引用外部模型 key；不保存 key 值 |
| `AI_ALLOW_MOCK_ENDPOINTS` | Mock endpoint 只有 `APP_ENV=test` 且显式设为 `1` 时允许；正式运行保持关闭 |
| `SESSION_SECRET_KEY` | Tax 浏览器 session secret；生产/预发布必须显式注入 |
| `COOKIE_SECURE` | 生产 HTTPS cookie 策略；不要为规避问题关闭安全 cookie |
| `INITIAL_ADMIN_PASSWORD` / `INITIAL_OPERATOR_PASSWORD` | 仅空库初始化时使用；遵守强密码校验，初始化后按组织流程更换 |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_RECYCLE_SECONDS` | Tax SQLAlchemy 连接池参数 |

### 5.4 RAG 关键变量

| 变量 | 运行规则 |
|---|---|
| `PROJECT_RAG_DATA_DIR` | 运行时 originals/parsed/cache 目录 |
| `PROJECT_RAG_IMPORT_ROOT` | 默认 `DATA_DIR/imports`；批量导入必须在受信目录内 |
| `PROJECT_RAG_ALLOWED_IMPORT_ROOTS` | 额外导入根目录白名单，逗号分隔；不能用 `/`、用户 Home、`/tmp` 或任意进程目录替代明确授权根 |
| `PROJECT_RAG_PROJECT_MATERIALS_ROOT` | V2 工程原始资料读取根；代码要求它保持在 V2.0 根目录下 |
| `PROJECT_RAG_MAX_UPLOAD_SIZE` | 上传大小字节数，默认 100 MiB（104857600） |
| `PROJECT_RAG_EMBEDDING_BACKEND` / `PROJECT_RAG_EMBEDDING_MODEL` | Embedding 后端和本地模型路径；V2 启动器默认指向 `V2.0/models/bge-m3` |
| `PROJECT_RAG_EMBEDDING_DIM` | 固定为 `1024`；修改前必须有显式 schema/migration 方案 |
| `PROJECT_RAG_RERANKER_BACKEND` / `PROJECT_RAG_RERANKER_MODEL` | Reranker 后端和模型路径；V2 启动器默认指向 `V2.0/models/bge-reranker-v2-m3` |
| `PROJECT_RAG_AUTO_START_WORKER` | 默认 `1`；RAG health 会检查 worker |
| `PROJECT_RAG_WORKER_POLL_SECONDS`、`PROJECT_RAG_WORKER_SHUTDOWN_TIMEOUT` | ingest worker 轮询与优雅停止参数 |
| `RAG_LLM_BASE_URL` / `RAG_LLM_MODEL` / `RAG_LLM_API_KEY` | RAG OpenAI-compatible 问答模型；无 base/model 时只能返回证据检索或明确未配置状态 |
| `RAG_REWRITE_LLM_*` / `RAG_HYDE_LLM_*` | Query Rewrite、HyDE 的独立覆盖配置；缺少模型时必须保留可识别的 unavailable 状态 |
| `PROJECT_RAG_ENABLE_REWRITE` / `PROJECT_RAG_ENABLE_HYDE` | 自适应检索开关，默认开启；按响应状态判断是否成功，不得静默伪造增强结果 |
| `PROJECT_RAG_GATE_TOP1_MIN`、`PROJECT_RAG_GATE_AVG_MIN`、`PROJECT_RAG_GATE_MIN_EVIDENCE` | Quality Gate 阈值 |
| `RAG_CSRF_ALLOWED_ORIGINS` / `RAG_PUBLIC_ORIGIN` | 浏览器 cookie 写操作的显式同源来源 |
| `RAG_COOKIE_SECURE` | RAG Web session cookie 的 HTTPS 策略 |
| `PROJECT_RAG_AUTH_REQUIRED` | 显式打开 RAG API auth；非回环绑定、受保护环境或配置了共享 key 时也会进入保护策略 |
| `PROJECT_RAG_RATE_LIMIT` / `PROJECT_RAG_LOG_LEVEL` | API 限流和日志级别 |

RAG 的 LLM 出站校验与一般 RAG/导入 URL 校验是两条边界：代码允许本地/内网 LLM（loopback/RFC1918；公网仍要求 HTTPS），但不能因此放宽文档导入、Facts bridge 或 RAG service URL 的 SSRF 策略。Tax 的本地/内网 LLM 例外由 `AI_ALLOW_PRIVATE_LLM=1` 明确打开。

### 5.5 Boss App 变量

`source_code/0.3_老板端安卓App_天府掌舵/.env.example` 当前提供 `VITE_API_BASE_URL` 和 `VITE_ENABLE_LOCAL_DEMO`。源码还支持：

| 变量 | 规则 |
|---|---|
| `VITE_TAX_API_BASE_URL` | 移动端登录和 JWT token 端点；未配置时默认 Tax `http://127.0.0.1:8921` |
| `VITE_API_BASE_URL` | RAG/Executive API 默认地址；未配置时默认 RAG `http://127.0.0.1:8922` |
| `VITE_ENABLE_LOCAL_DEMO` | 只有 Vite DEV 且显式为 `true` 才允许本地 demo 登录；正式构建不得依赖它，不能把本地 demo session 当生产认证 |
| `CDJG_RELEASE_STORE_FILE` 等签名变量 | Release 构建时从环境注入 keystore 路径和口令，仓库不得保存签名材料 |

## 6. PostgreSQL 与迁移

### 6.1 启动和确认 PostgreSQL

```bash
cd "/Users/yvoche/AI开发/073_成都建工/V2.0"
./start_postgres.sh
pg_isready -h 127.0.0.1 -p 5432
```

`start_postgres.sh` 管理的是 `V2.0/data/postgres`。它只负责本机进程，不会替代 `projectrag` 数据库创建或业务 seed。停止 PostgreSQL 前先停止 Tax/RAG 并确认没有其他业务使用该实例：

```bash
./stop_all.sh
./stop_postgres.sh
```

### 6.2 迁移顺序

正式架构是 PostgreSQL-only。升级顺序固定为：

1. Tax Alembic：`001_initial` → `002_request_id` → `003_b04_tax_id` → `57eaaaffc6eb` → `58c1` → `59d2` → `60a1` → `61f` → `62_ai_review_batch_status`。
2. RAG Alembic：`001_initial` → … → `010_analytics_contract_views` → `011_entity_mapping_facts_gate` → `012_document_storage_paths`。

一键迁移并启动：

```bash
cd "/Users/yvoche/AI开发/073_成都建工/V2.0"
./start_all.sh --migrate
```

若只需显式执行迁移，使用当前两个源码目录的直接命令，并在完成后单独启动服务：

```bash
TAX_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0/source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0"
RAG_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0/source_code/0.2_RAG系统/project-rag-v1.1"

(cd "$TAX_DIR" && uv run alembic -c alembic.ini upgrade head)
(cd "$RAG_DIR" && "$RAG_DIR/.venv/bin/python" -m alembic -c alembic.ini upgrade head)
```

迁移后核对两个独立版本表：

```sql
SELECT * FROM alembic_version_tax;
SELECT * FROM alembic_version_rag;
```

不要查询或修改一个通用 `alembic_version` 来推断双套迁移状态。

### 6.3 正式迁移前门禁

- `pg_dump -Fc` 备份 `projectrag`，同时保存 `sha256` 和 `pg_restore --list` 输出。
- 在独立数据库执行同一迁移链，并验证 `upgrade head`、必要的 `downgrade`/恢复路径和 schema diff。
- 迁移前后核对表、视图、FK、向量维度、主体/项目计数和关键数据摘要。
- 正式库迁移期间停止写入服务或按已批准的维护窗口执行。
- 任何回滚都必须先确定目标 revision 和依赖；不能凭感觉直接 downgrade 正式库。通常先处理依赖 RAG，再处理 Tax，具体以迁移文件和备份验证为准。

## 7. 服务启动、停止与健康检查

### 7.1 权威启动

```bash
cd "/Users/yvoche/AI开发/073_成都建工/V2.0"
./start_all.sh                 # 只启动，不迁移、不 seed
./start_all.sh --migrate       # 显式迁移 Tax→RAG 后启动
./start_all.sh --skip-postgres # PostgreSQL 已由外部服务管理时使用
./start_all.sh --help
```

启动脚本会：

- 检查 PID 文件和端口，不盲目杀其他进程；
- 要求 Tax/RAG 数据库 URL 是 PostgreSQL；
- 校验 JWT trust domain；
- 选择并检查本地 BGE-M3/Reranker 模型路径；
- 默认不使用 `--reload`；
- 启动后等待 RAG `/api/v1/health` 与 Tax `/healthz`；
- 启动失败时回收本次启动的服务进程。

### 7.2 停止

```bash
cd "/Users/yvoche/AI开发/073_成都建工/V2.0"
./stop_all.sh
```

`stop_all.sh` 只处理 PID 文件确认属于本 V2.0 的 uvicorn 进程，不按端口盲杀；它不修改 PostgreSQL schema 或数据。默认端口：Tax `8921`，RAG `8922`。

### 7.3 健康和日志

```bash
curl --fail --silent http://127.0.0.1:8921/healthz | python3 -m json.tool
curl --fail --silent http://127.0.0.1:8922/api/v1/health | python3 -m json.tool

tail -f "/Users/yvoche/AI开发/073_成都建工/V2.0/tax_runtime.log"
tail -f "/Users/yvoche/AI开发/073_成都建工/V2.0/rag_runtime.log"
lsof -nP -iTCP:8921 -sTCP:LISTEN
lsof -nP -iTCP:8922 -sTCP:LISTEN
```

RAG readiness 至少要有 DB、Embedding、Reranker 正常，Worker 不能是 down；Facts 在无项目映射或无 Snapshot 时可以是 degraded，不应因此被脚本当成数据库/模型故障。Tax `/healthz` 会聚合 db、rag、facts、ai 组件，逐项看 `components`，不能只看顶层 HTTP 200。

## 8. 入口与 API 目录

### 8.1 Tax（8921）

- 浏览器入口：`http://127.0.0.1:8921/`
- OpenAPI：`http://127.0.0.1:8921/docs`
- 健康：`GET /healthz`
- 用户认证：`POST /api/v1/auth/token`、`POST /api/v1/auth/refresh`、`GET /api/v1/auth/verify`；浏览器登录使用 `/login`。
- 项目/经营 JSON：`GET /api/projects/{pid}`、`GET /api/projects/{pid}/matching`。
- 税务台账：`GET /tax-ledger`（HTML 只读）、`GET /api/tax-ledger`（JSON 只读，若版本路由已注册以 OpenAPI 为准）、`POST /tax-ledger/rebuild`（显式重建，需角色）。
- 核心页面：`/dashboard`、`/project-list`、`/project/{pid}`、`/tax-ledger`、`/risks`、`/imports`、`/ai-review`、`/health-check`、`/tasks`、`/ai-prompts`、`/ai-models`、`/audit`。
- Tax↔RAG 桥：`/rag-sync/connect`、`/rag-sync/status`、`/rag-sync/sync`、`/rag-sync/facts/{project_id}`、`/rag-sync/project-map` 等，具体参数以当前 `/docs` 为准。

Tax 的税务台账 GET 不能触发重建或写库；重建必须由明确的 `POST /tax-ledger/rebuild` 完成，并记录审计。

### 8.2 RAG（8922）

- 浏览器入口：`http://127.0.0.1:8922/`
- OpenAPI：`http://127.0.0.1:8922/docs`
- 健康：`GET /api/v1/health`、`GET /healthz`
- 文档：`POST /api/v1/documents/upload`、`POST /api/v1/documents/import-folder`、`GET /api/v1/documents/{id}`、`POST /api/v1/documents/{id}/parse`、`DELETE /api/v1/documents/{id}`。
- 检索：`POST /api/v1/retrieve`、`POST /api/v1/query`、`POST /api/v1/retrieve/adaptive`、`POST /api/v1/retrieve/deep`、`POST /api/v1/retrieve/explain`。
- 法规：`/api/v1/regulations`、`/api/v1/regulations/retrieve`、`/api/v1/regulations/query`。
- 项目/主体：`/api/v1/projects`、`/api/v1/projects/sync`、`/api/v1/entities`、`/api/v1/external-parties`。
- Facts：`GET /api/v1/facts/projects/{project_code}`、`GET /api/v1/facts/projects/{project_code}/history`、`POST /api/v1/facts/invalidate`。
- AI Review：RAG 内置 `/api/v1/ai-review/*` 路由以当前 OpenAPI 为准；Tax 的单环节页面/API 仍在 8921。
- Executive：`GET /api/v1/executive/cockpit/summary`、`GET /api/v1/executive/projects`、`GET /api/v1/executive/projects/{id}/360`、`GET /api/v1/executive/entities/matrix`、`POST /api/v1/executive/ai/chat`、`POST /api/v1/executive/ai/chat/stream`。

### 8.3 Boss App

Boss App 使用 Tax 的 JWT 登录端点，随后向 RAG Executive API 发 Bearer 请求。源码目录：`source_code/0.3_老板端安卓App_天府掌舵`。

```bash
cd "/Users/yvoche/AI开发/073_成都建工/V2.0/source_code/0.3_老板端安卓App_天府掌舵"
npm ci
npm test
npm run lint
npm run build
npx cap sync android
```

生产构建必须使用真实后端认证、HTTPS/受控 Cloudflare Access 等已批准入口；不要把 Vite DEV 的本地 demo 登录、演示 session 或无 API 假数据当作生产能力。

## 9. 认证、RBAC 与两种凭据

### 9.1 Tax JWT

- Tax 是用户认证和 JWT 签发者；RAG 只验证 Tax 签发的 access JWT，不持有用户密码、不签发用户 token。
- JWT 算法为 HS256；access token 默认 60 分钟，refresh token 默认 7 天（以当前源码为准）。
- Tax 用户角色当前为 `admin`、`operator`；普通登录后才能访问受保护页面/API。
- `admin` 才能管理用户、AI 模型端点、Prompt、规划等 admin-only 操作；`admin`/`operator` 可访问读操作和税务台账重建等明确授权的业务操作。
- 未登录通常为 401；已登录但角色不足为 403。不要通过放宽依赖或关闭 middleware 解决。

### 9.2 RAG Web/API RBAC

- RAG Web session 由同源 session bridge 向 Tax 换取 token，并使用 HttpOnly cookie；浏览器 mutation 还要求同源/CSRF 校验。
- RAG Executive 路由要求 Tax JWT 且 allow-list 为 `admin`、`operator`；低权限/`viewer` 不应获得 Executive 数据，预期为 403。
- Facts 与 RAG AI Review bridge 是服务间保护面，必须使用 `RAG_SHARED_API_KEY`；Tax JWT 不能代替 shared key。
- RAG 的 `Authorization: Bearer <shared-key>` 或受保护路由要求的 `X-API-Key` 只在授权的服务间调用中使用；不要把 shared key 放进浏览器/localStorage。
- 缺 key、key 错、JWT 过期、角色不足必须保留 401/403 语义，不得回退到演示数据。

### 9.3 验证样例

以下只展示变量名和占位符，不输出真实 secret：

```bash
export RAG_SHARED_API_KEY='<injected-only-in-current-shell>'
curl -i \
  -H "Authorization: Bearer ${RAG_SHARED_API_KEY}" \
  "http://127.0.0.1:8922/api/v1/facts/projects/YB-DEMO-001"
unset RAG_SHARED_API_KEY
```

验证报告只记录 HTTP 状态、响应中的 `status`/`facts_available` 和 key 的存在性，不记录 key 本身。

## 10. Canonical Entity Master 与项目映射

### 10.1 主体口径

系统内 canonical codes 固定为：

```text
A01-A11  (11)
B01-B10  (10)
C01-C02  (2)
D01-D03  (3)
合计 26；legal_entity=true 合计 25
```

业务角色映射为：A=施工、B=商贸物资、C=建筑劳务、D=机械设备。业务角色不是法人代码。

`A04` 是四川屹明汇建设工程有限公司重庆分公司，`legal_entity=false`，`parent_entity_code=A03`。确定性税务计算、主体归集和 Facts 口径必须按主数据将 A04 归集至 A03；不能把 A04 当成独立纳税法人，也不能把 A03/A04 混成单字 A。

### 10.2 6 个项目待办

当前正式库 6 个项目都未映射主体。这是 fail-closed 的数据质量状态：

- Facts Provider 返回 `DEGRADED`，`facts_available=false`，不生成占位财务指标。
- Executive API 在 Facts 不可用时，经营金额/指标保持 `null` 或空对象，并标注 `data_source=unavailable`。
- AI Review 必须把主体映射缺口列为人工待办，不能自行猜测。
- 完成人工确认后，使用项目映射 API/后台流程写入合法 canonical code，并重新失效/生成 Facts；写入前要保留审批和审计证据。

主体映射完成后的验证至少包括：项目 `entity_code` 为合法 26-code、A04 仍非独立且 parent 为 A03、没有孤儿 FK、Analytics view 能按项目返回完整必需指标、Facts 状态变为 `AVAILABLE` 前没有其他数据缺口。

## 11. Analytics、Facts 和 AI Review 状态语义

### 11.1 Facts Provider

`GET /api/v1/facts/projects/{project_code}` 正常采用 HTTP 200 返回机器可读状态；业务是否可用看响应体，不只看 HTTP 状态：

```json
{
  "status": "DEGRADED",
  "facts_available": false,
  "metrics": {},
  "reason": "..."
}
```

`DEGRADED` 表示 Analytics/Canonical Facts 不足、映射无效、Snapshot 缺失或依赖暂时不可用；它不是可用财务结果。不得用 0、历史 demo 数字、平均值或 LLM 推断填充 `metrics`。

### 11.2 Executive API

当任一必需项目 Facts 不完整时，Executive 聚合不会输出部分集团金额作为完整集团结论。检查 `_meta`：

- `data_source=live` 且 `facts_available=true`：才可把指标当作当前 Facts 结果继续处理。
- `data_source=unavailable` 且 `facts_available=false`：所有依赖 Facts 的金额/指标应为 `null` 或空结构，并保留 `facts_reason`。
- `snapshot`/`demo` 只能在明确、受控的演示边界中识别；正式业务 Agent 不得把它升级为真实底账。

### 11.3 AI Review

- AI Review 输入应包含项目 Facts 状态、数据缺口、RAG 证据和 prompt/model 版本。
- Facts unavailable、RAG bridge 401/503、模型未配置、解析失败或结果无法可靠解析时，结果要标为 `DEGRADED`/`UNAVAILABLE`，并要求人工复核。
- `READY` 只表示当前实现认为结果可供进一步审阅，不等于税务申报或管理层正式结论。
- AI 建议不得修改 `tax_ledgers`、`analytics_*`、Canonical Facts 或原始凭证。

## 12. 自适应检索与本地 LLM

### 12.1 完整检索链

对 Query Rewrite、Quality Gate、HyDE、Hybrid Retrieval、Rerank 的每个阶段都要检查响应中的阶段状态和错误字段。V2.0 已提供：

- `/api/v1/retrieve/adaptive`：完整自适应检索入口；
- `/api/v1/retrieve/deep`：深度链路入口；
- `/api/v1/retrieve/explain`：解释各阶段决策/证据来源。

调用参数按 OpenAPI schema 的字段名传递；不要依赖位置参数或旧 V0.3 签名。任何阶段 unavailable 都要在 Agent 报告中保留，不得静默说“检索成功”。

### 12.2 本地/内网模型

- Tax：`AI_ALLOW_PRIVATE_LLM=1` 允许 loopback/RFC1918 AI endpoint 使用 HTTP 或 HTTPS；link-local、metadata、multicast、reserved、unspecified 仍拒绝。公网 host 必须 HTTPS，并受 `AI_ALLOWED_HOSTS` 约束。
- RAG：OpenAI-compatible LLM validator 对 loopback/RFC1918 模型提供独立允许面；这不等于允许任意内部 URL。RAG service、Facts bridge、文档导入仍遵守更严格 URL/路径校验。
- RAG 启动时使用本地 BGE-M3/BGE-Reranker；Embedding 维度固定 1024。直接运行 RAG 时应设置绝对模型路径；使用 `start_all.sh` 时由脚本从 `V2.0/models/` 解析并验证。
- 模型调用超时、模型未配置、返回格式错误都必须表现为明确错误/降级状态；不得改成固定“成功”或固定风险等级。

## 13. 文档上传与导入安全边界

- 上传接口受 `PROJECT_RAG_MAX_UPLOAD_SIZE` 限制，默认 100 MiB；超限应拒绝。
- `import-folder` 只能读取 `PROJECT_RAG_IMPORT_ROOT`、`DATA_DIR` 和明确列入 `PROJECT_RAG_ALLOWED_IMPORT_ROOTS` 的目录；目录必须经过 `resolve()` 后仍在允许根内。
- 不允许把 `/`、用户 Home、`/tmp`、macOS 进程目录、任意外部挂载作为默认可信根。
- 原始文件路径必须通过 V2 `project_materials` 根规则解析；文档删除后不能留下孤儿 Chunk/embedding。
- 外部 URL 解析/法规导入遵守 SSRF 校验、HTTPS 和重定向限制；不能因内部使用而关闭这些校验。

## 14. 测试、Lint 和构建门禁

### 14.1 Tax

Tax 集成测试只接受显式 disposable PostgreSQL URL，数据库名必须包含 `test`；没有 `TEST_DATABASE_URL` 的集成测试会 skip，不能把这种结果报告为完整通过。

```bash
TAX_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0/source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0"
cd "$TAX_DIR"
uv sync
TEST_DATABASE_URL="postgresql+psycopg://<user>:<password>@127.0.0.1:5432/projectrag_tax_v2_test" uv run pytest -q
uv run pytest --collect-only -q
uv run ruff check app tests
python -m compileall -q app
```

### 14.2 RAG

RAG 的 DB 测试同样只能使用显式 disposable PostgreSQL；不得使用正式 `projectrag`，也不得使用 `projectrag_xianyu`。

```bash
RAG_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0/source_code/0.2_RAG系统/project-rag-v1.1"
cd "$RAG_DIR"
uv sync --inexact
TEST_DATABASE_URL="postgresql+psycopg://<user>:<password>@127.0.0.1:5432/projectrag_rag_v2_test" uv run pytest -q
uv run pytest --collect-only -q
uv run ruff check app ai_review facts_provider analytics_contract tests
python -m compileall -q app ai_review facts_provider analytics_contract
```

默认 pytest 会排除历史 SQLite 测试。只有为历史复现明确指定 `pytest -o addopts='' tests/_archive_sqlite` 才能运行；其结果不能作为 V2.0 PostgreSQL 门禁。

### 14.3 Boss App

```bash
BOSS_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0/source_code/0.3_老板端安卓App_天府掌舵"
cd "$BOSS_DIR"
npm ci
npm test
npm run lint
npm run build
npx cap sync android
cd android
./gradlew test lintRelease assembleRelease
```

没有 Android SDK/JDK 或未配置签名时，只能报告环境受限或未签名产物，不能写成正式 Release 通过。

### 14.4 运行态 E2E 最小门禁

在正式库只做读检查和真实 HTTP 验证，不用测试 fixture 写正式数据。至少检查：

1. Tax/RAG 进程命令行无 `--reload`，端口为预期值。
2. 两端 health、RAG DB/Embedding/Reranker/Worker 状态正常。
3. Facts 无 key/错 key 为 401；正确 key 可访问；6 个未映射项目为 `DEGRADED` 且 `metrics={}`。
4. Executive viewer/无效 JWT 为 401/403，admin/operator 按角色工作，Facts unavailable 时金额为 `null`。
5. Tax 台账 GET 前后数据库摘要/行数不变；需要重建时只能调用显式 POST。
6. adaptive/deep/explain 路由能返回结构化阶段状态。
7. AI health/异步任务最终状态、并发锁、stale lease 和 `running=0` 可验证。
8. 日志无 traceback、HTTP 500、unique conflict、SQLite fallback、敏感值回显。

## 15. 备份、恢复与数据安全

### 15.1 已保存备份

| 文件 | SHA-256 |
|---|---|
| `database/backups/projectrag_formal_pre_document_path_20260824_154709+0800.dump` | `0b657d5061ecc5cfc227735127aa362769f919dd323b87af13d60a04c6c1d75c` |
| `database/backups/projectrag_formal_pre_project_mapping_20260824_003317+0800.dump` | `3c2b976fe8f53e3a197414d2f1a0f45304e776f6e73c627805f6905d647fb491` |
| `database/backups/projectrag_formal_pre_rag011_20260823_231018+0800.dump` | `654f5a0ac8601b72330272da83c57a97cf3b9116e7421c8a975aca97781d4930` |
| `database/backups/projectrag_formal_pre_rag010_20260823_224034+0800.dump` | `404cdb4fcc24d60cba21dd19f0bffc37c831a326d13b44f7173be4a219e6daf6` |

验证：

```bash
cd "/Users/yvoche/AI开发/073_成都建工/V2.0"
shasum -a 256 database/backups/projectrag_formal_pre_document_path_20260824_154709+0800.dump
pg_restore --list database/backups/projectrag_formal_pre_document_path_20260824_154709+0800.dump | sed -n '1,80p'
```

### 15.2 新建备份

```bash
BACKUP="/Users/yvoche/AI开发/073_成都建工/V2.0/database/backups/projectrag_formal_pre_change_$(date +%Y%m%d_%H%M%S%z).dump"
pg_dump --format=custom --file="$BACKUP" "postgresql://<db-user>@127.0.0.1:5432/projectrag"
shasum -a 256 "$BACKUP" | tee "$BACKUP.sha256"
pg_restore --list "$BACKUP" > "$BACKUP.list"
```

### 15.3 恢复原则

先恢复到新建的 disposable 数据库并做 `pg_restore --list`、migration head、表/视图/FK/数据摘要核对。只有在父 Agent/用户明确批准、维护窗口已确认且当前正式库已有新备份后，才可对 `projectrag` 做恢复。恢复前停止 Tax/RAG，恢复后按 Tax→RAG 顺序核对并启动。

禁止直接对正式库运行未经验证的 `DROP DATABASE`、`pg_restore --clean`、schema truncate 或全量 seed。任何恢复操作都要保存命令、时间、备份 SHA、目标数据库和恢复后的只读核对结果。

## 16. 故障排查决策表

| 症状 | 先查什么 | 正确处理 |
|---|---|---|
| `start_all.sh` 报 JWT 不一致/长度不足 | 两端环境变量是否为空/不同；只比较长度或哈希前缀，不打印值 | 从 secret manager 重新注入同一个 `JWT_SECRET_KEY`，不要生成临时 fallback |
| RAG `/api/v1/health` 的 embedding/reranker down | `config.json`、绝对路径、模型文件权限、1024 维配置 | 修正 `PROJECT_RAG_*_MODEL` 或 V2 模型目录，重启并观察预热日志 |
| Facts 200 但 `DEGRADED` | `entity_code`、A04 parent、analytics view、必需指标、Snapshot | 记录 `reason`，补数据/映射/迁移；禁止改成 0 或 demo 数字 |
| Facts/AI Review 401 | shared key 是否注入 Tax/RAG；header 类型是否正确 | 使用同一 `RAG_SHARED_API_KEY`；JWT 不能代替 service key |
| Executive 403 | Tax JWT 是否有效、角色是否 `admin`/`operator` | 按 RBAC 申请正确角色；不要改 middleware allow-list |
| Tax health 的 RAG/facts degraded | RAG health、共享 key、Facts response、RAG URL | 逐层修复依赖；保留降级状态，不能让 Tax 伪报 healthy |
| import-folder 被拒绝 | realpath 是否位于允许根，文件是否超限 | 配置明确的 import root；不要把根目录、Home、`/tmp` 加入白名单 |
| query rewrite/HyDE unavailable | RAG LLM base/model、阶段响应、timeout | 明确报告阶段 unavailable；可关闭某阶段做受控对比，但不能静默吞错 |
| LLM URL 被拒绝 | scheme、DNS 解析结果、是否 metadata/link-local/private、对应服务策略 | Tax 内部模型设置 `AI_ALLOW_PRIVATE_LLM=1`；RAG 只使用其 LLM 专用允许面，不能关闭通用 SSRF 校验 |
| 端口占用/PID 异常 | `.tax.pid`、`.rag.pid`、`ps`、`lsof` | 只处理能确认是 V2.0 uvicorn 的进程；未知 PID 先人工确认 |
| 测试大量 skipped | `TEST_DATABASE_URL` 是否显式指向 disposable PostgreSQL | 补建测试库并重跑；报告中把 skipped 标为未验证 |
| 日志出现 traceback/500/unique conflict | 关联 request id、迁移 head、最近修改和 DB 约束 | 保留原始日志，独立复现后窄修复；不通过吞异常或删测试解决 |

## 17. 明确禁止事项

- 禁止连接或操作 `projectrag_xianyu`。
- 禁止使用 SQLite 作为 V2.0 运行、迁移或主测试后端。
- 禁止在正式库运行 `TAX_SEED_MODE=demo`、历史 demo seed、`make demo` 或会清库的初始化脚本。
- 禁止把真实数据缺口用硬编码金额、固定风险等级、`888888` demo 登录、空 catch 或静默 fallback 掩盖。
- 禁止把 A/B/C/D 作为法人主体、付款方、税号或默认实体；必须使用完整 canonical code。
- 禁止在主体映射不明确时猜测项目归属；6 个项目当前必须人工映射。
- 禁止把 `TAX_RAG_API_KEY`、JWT、浏览器 cookie、Boss localStorage token 当作 shared key 的替代品。
- 禁止打印、提交或写入本指南真实 secret、模型 API key、数据库口令、JWT 或 bearer key。
- 禁止为了通过测试关闭认证、RBAC、CSRF、上传大小、import-root、SSRF 或 HTTPS 校验。
- 禁止在正式运行使用 `uvicorn --reload`；使用 `start_all.sh` 的无 reload 方式。
- 禁止未经备份、隔离验证和明确授权修改正式数据库。
- 本子任务未授权 Git commit、push、发布或删除他人文件；维护 Agent 应保持当前修改并单独报告 Git 状态。

## 18. Agent 交接格式

每次执行结束使用以下结构，不省略证据：

```text
MODIFIED FILES
- absolute/path/to/file

CHANGES
- narrow change and affected boundary

TESTS RUN
- exact command

TEST RESULTS
- PASS / FAIL / SKIPPED / ENVIRONMENT-LIMITED
- counts and relevant evidence

DATABASE / RUNTIME EVIDENCE
- database target (never include password)
- migration heads
- health endpoints and status
- data hashes/counts when relevant

REMAINING RISKS
- unresolved mapping, unavailable dependency, unverified rollback, or none
```

最后必须说明：是否修改正式库、是否创建/删除测试库、服务是否仍运行、Git 是否提交。若没有独立 `final_reviewer` 的只读证据，不得把“测试通过”升级为“最终交付通过”。
