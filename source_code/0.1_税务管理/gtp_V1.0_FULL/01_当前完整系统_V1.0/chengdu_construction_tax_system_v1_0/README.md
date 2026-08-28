# 成都建筑施工企业综合经营与税务统筹系统 V1.0

> 本目录是成都建工 V2.0 集成系统中**当前运行的 Tax V1.0 服务源码**，不是 V0.2 的只读快照。数据库结构由 Alembic 管理，业务事实通过共享 PostgreSQL 与 RAG / Analytics / FactsProvider 协同。

## 当前运行边界

系统以 canonical Entity 主数据作为唯一内部主体边界，使用 26 家系统内单位代码：A01-A11、B01-B10、C01-C02、D01-D03。A04 为 A03 的非独立法人分支机构，确定性税务计算按 `parent_entity_code` 归集到 A03。

A/B/C/D 只表示业务角色，不是法人代码，也不能作为计算、过滤或四流证据的默认主体。系统外交易方必须来自 `ExternalParty` 或可追溯的来源凭证；无法唯一识别时保留 unresolved/unknown 状态，不用虚拟代码替代。

## 在成都建工 V2.0 中的职责

Tax 服务负责：

- 项目、合同、履约、发票、资金、真实成本等确定性经营数据；
- 四流证据匹配与可追溯风险扫描；
- 法人月度管理税务台账；
- Planning 场景测算，但不把局部场景余量冒充项目最终利润；
- 与 Analytics / FactsProvider 共用 canonical EAC 与事实口径；
- 为老板端和 RAG 提供可验证的确定性事实来源。

AI 只用于审查、解释和建议，不覆盖确定性财务、税务和四流结果。

## 版本与迁移

当前代码目录名保留 `gtp_V1.0_FULL/.../chengdu_construction_tax_system_v1_0`，用于兼容既有启动器和部署路径。历史 V0.1/V0.2 文档仅用于追溯架构演进，不代表当前运行代码仍与旧快照逐字节一致。

数据库迁移按 Alembic revision 顺序执行。成都建工 V2.0 根启动器在 `--migrate` 模式下先执行 Tax migration，再执行 RAG migration，以满足共享函数和 Analytics 视图依赖。

## 快速启动

推荐从仓库根目录统一启动：

```bash
./start_all.sh
```

数据库结构发生变化后显式执行：

```bash
./start_all.sh --migrate
```

仅调试 Tax 服务时，也可进入本目录使用现有 `Makefile` / `run_demo.sh`。默认 Web 端口为 `8921`，可通过 `TAX_PORT` 覆盖。

## 测试

测试数据库必须是独立 PostgreSQL 测试库，数据库名包含 `test`：

```bash
TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/projectrag_test \
  uv run pytest -q
```

静态检查：

```bash
uv run ruff check .
```

## 核心页面与接口

| 路由 | 功能 |
|---|---|
| `/` | 经营驾驶舱 |
| `/project/{id}` | 项目经营页 |
| `/matching` | 四流匹配 |
| `/tax-ledger` | 实际法人税务管理台账 |
| `/risks` | 风险中心 |
| `/imports` | 数据导入 |
| `/ai-review` | AI 检查 |
| `/health-check` | 项目综合体检 |
| `/tasks` | 整改任务中心 |
| `/ai-prompts` | Prompt 模板版本库 |
| `/ai-models` | 模型端点管理 |
| `/audit` | 操作审计 |
| `/docs` | OpenAPI |
| `/healthz` | 健康检查 |

## Tax ↔ RAG

Tax 默认连接本机 RAG `http://127.0.0.1:8922`。两端必须使用同一共享 PostgreSQL 事实库，并由各自运行环境注入必要的服务凭据和 JWT 配置。根启动器负责统一读取环境并启动两个服务。

如果 Tax 与 RAG 部署在不同主机，使用管理员系统参数配置 RAG 服务地址，并通过内置连接测试确认后再保存。

## 确定性计算原则

1. 每个内部单位的代码、名称、法人状态和归属来自 canonical Entity 主数据。
2. A/B/C/D 仅为业务角色，不能成为法人代码或缺失数据的替代值。
3. 项目综合经营口径抵消系统内交易，并穿透到真实外部成本。
4. 外部交易方必须可追溯；无法识别时显式保留数据缺口。
5. 金额计算使用 Decimal / PostgreSQL Numeric；EAC、税务和四流由确定性程序计算。
6. Invoice 与 RealCost 只有在存在显式来源关联时才允许防重复计成本；金额相同不是来源证据。
7. 风险扫描只替换自身 `source + rule_version` 生成的事件，不删除人工或其他来源事件。
8. 业务阈值和允许值来自数据库规则配置；Python 不保存隐藏的财务阈值默认值。
9. AI 不得覆盖确定性结果。

## 主要文档

- `docs/POSTGRESQL_ONLY_ARCHITECTURE.md` —— PostgreSQL-only 架构
- `docs/AI_REVIEW_ARCHITECTURE.md` —— AI 审查架构
- `docs/PRODUCTION_ROADMAP.md` —— 生产化路线图
- `CHANGELOG_V02.md` / 历史文档 —— 仅用于版本演进追溯
