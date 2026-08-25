# 成都建筑施工企业综合经营与税务统筹系统 V1.0

> **主体口径**：当前运行版本以 canonical Entity 主数据为唯一法人边界，使用 26 家系统内单位代码（A01-A11、B01-B10、C01-C02、D01-D03）；A04 为 A03 的非独立法人分支机构。旧快照中的单字主体仅属于历史语境，不纳入当前运行路径。

## 实际单位与业务角色

系统内法人必须来自以下 canonical Entity 代码：A01-A11、B01-B10、C01-C02、D01-D03。

代码前缀只用于标识主数据中的业务角色聚合，不是虚拟公司：A=施工，B=商贸物资，C=建筑劳务，D=机械设备。页面、接口和计算必须提交完整的实际单位代码（例如 A08、B01），不得提交单字角色。

A04（四川屹明汇建设工程有限公司重庆分公司）为非独立法人，`parent_entity_code=A03`；确定性税务计算按主数据规则归集到 A03。系统外交易方使用 `ExternalParty` 中的真实名称和外部代码；无法唯一识别的交易方必须保留 unresolved/unknown external party，不能用占位符代替。

## V0.2 主要改进（继承自 V0.1 优化建议）

完整的功能说明、架构演进与改进清单，详见：

- `CHANGELOG_V02.md` —— V0.2 变更日志
- `CHANGELOG_V01.md` （在 `gtp_V0.2_FULL/.../chengdu_construction_tax_system_v0_2/` 内） —— V0.1 变更日志

> 因 V1.0 是 V0.2 的逐字节副本，**V1.0 自身没有独立的 changelog**。本目录内仅保留 `CHANGELOG_V02.md` 与 `CHANGELOG_V1.0.md`（本快照说明）。

## V1.0 快照用途

| 用途 | 说明 |
|---|---|
| **存档** | 留存 V0.2 在某一时刻的完整状态，便于复现 |
| **回滚** | 若 V0.2 包损坏，可从本包恢复 |
| **分发** | 与 V0.2 同步发版；下游可直接引用同一份说明 |
| **离线对比** | 与 V0.2 包对比应零差异 |

## 与 V0.2 包的关系

```
gtp_V0.2_FULL/01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/   ← 主版本
gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/   ← 本包（快照副本）
```

代码 100% 一致。如需在 V0.2 上做新功能迭代，请直接修改 V0.2 包，不要在本包上写代码。

## 快速启动

```bash
cd 01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0

# 一键启动
make demo
# 或
./run_demo.sh
# 或双击 run_demo_mac.command

# 浏览器
open http://127.0.0.1:8921

# 如需改端口，脚本和 Makefile 均支持 TAX_PORT
# TAX_PORT=8925 ./run_demo.sh
```

## 测试

```bash
make test
# 或
TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/projectrag_test python -m pytest -q
```

## 核心页面

| 路由 | 功能 |
|---|---|
| `/` | 经营驾驶舱 |
| `/project/{id}` | 项目经营页 |
| `/matching` | 四流匹配 |
| `/tax-ledger` | 实际法人税务管理台账 |
| `/risks` | 风险中心 |
| `/imports` | CSV 导入 |
| `/ai-review` | 单环节 AI 检查 |
| `/health-check` | 项目一键综合体检（quick/standard/deep） |
| `/tasks` | 整改任务中心（含复检） |
| `/ai-prompts` | Prompt 模板版本库 |
| `/ai-models` | 外部模型 API 端点 |
| `/audit` | 操作审计（含 actor/ip） |
| `/docs` | FastAPI 自动 OpenAPI |
| `/healthz` | 健康检查 |

## 外部模型 API

数据库**只保存环境变量名**，不保存 Key：

```bash
export TAX_AI_API_KEY="sk-..."
./run_demo.sh
```

适配器：`mock`（离线）和 `openai_compatible`（真实 HTTP）。

## Tax ↔ RAG 环境注入

Tax 默认连接本机 RAG `http://127.0.0.1:8922`。两端进程必须分别从各自的
运行环境/secret manager 注入同名变量 `RAG_SHARED_API_KEY`，且值必须一致；
不要把一端的 `.env` 文件复制到另一端，也不要把 `TAX_RAG_API_KEY` 当作共享密钥。
`TAX_RAG_API_KEY` 如因旧端点兼容需要保留，只能作为独立的端点凭据，不能回退或
 派生 `RAG_SHARED_API_KEY`。生产、预发布、自动同步或显式认证模式缺少共享密钥时，
Tax 应拒绝启动。

如果 Tax 与 RAG 部署在不同电脑，登录 Tax 后打开右上角“系统参数设置”，在
“RAG 知识库连接（管理员）”中填写 RAG 的完整 IP 地址或域名，先测试连接，再保存设置。
本机或公司内网地址必须由管理员明确批准；Tax 会保存并复核 DNS 地址快照，检测到域名解析
变化时会停止出站请求并要求重新批准。RAG shared key 始终只由 Tax 服务端的
`RAG_SHARED_API_KEY` 提供，不在浏览器设置页面中填写或传输。

## 重要边界

1. 每个实际法人代码、名称和统一社会信用代码来自 canonical Entity 主数据，法律、会计和税务独立
2. A/B/C/D 只作为 `business_role` 业务角色，不作为法人代码或默认主体
3. 项目综合经营口径抵消实际单位之间的内部交易并穿透真实底层成本
4. 外部交易方必须来自真实 ExternalParty 主数据或来源凭证；无法识别时保持 unknown external party
5. 税额、项目利润、四流、EAC 由确定性程序计算（金额 Numeric）
6. AI 只审查、解释、建议，**不得覆盖确定性结果**
7. Demo 税务规则默认 `reviewed=False`，**不可直接申报**
8. 风险阈值可由 `RiskThreshold` 表调整

## 文档

- `docs/V02_DESIGN.md` —— V0.2 设计说明
- `docs/V02_TEST_REPORT.md` —— V0.2 测试报告
- `docs/V02_MIGRATION.md` —— V0.1 → V0.2 迁移指南
- `docs/AI_REVIEW_ARCHITECTURE.md` —— AI 编排架构
- `docs/PRODUCTION_ROADMAP.md` —— 生产化路线图


## PostgreSQL-only 架构

当前正式架构仅支持 PostgreSQL；数据库结构由 Alembic 管理。详见 `docs/POSTGRESQL_ONLY_ARCHITECTURE.md`。
