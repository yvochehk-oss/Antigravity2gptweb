# V1.0 完整资料包

> **本包性质**：V1.0 是 **V0.2 的镜像快照副本**，代码与 `gtp_V0.2_FULL/01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/` **逐字节一致**（`diff -r` 零差异）。
>
> 本目录**仅作存档 / 应急回滚 / 离线分发之用**，不引入新的功能改动。

## 目录结构

```
gtp_V1.0_FULL/
├── 01_当前完整系统_V1.0/
│   └── chengdu_construction_tax_system_v1_0/   # 可运行代码（V0.2 镜像快照）
├── 02_总体设计与指导文件/                         # 总体设计 Word 文档
├── 03_历史版本归档/                              # V0.1 副本对照（保留）
├── README.md                                    # 顶层说明（本文件）
├── README_完整资料包说明.md                      # 资料包说明
└── FILE_MANIFEST.txt                            # 文件清单
```

## V1.0 与 V0.2 包的关系

| 维度 | 值 |
|---|---|
| V1.0 包路径 | `gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/` |
| V0.2 包路径 | `gtp_V0.2_FULL/01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/` |
| 代码差异 | **0**（`diff -r` 逐字节一致） |
| 路由差异 | **0**（V1.0 与 V0.2 均为 55 个路由） |
| 数据模型差异 | **0**（模型定义、迁移脚本完全一致） |
| 测试差异 | **0**（同一份 `tests/`，同一份 `conftest.py`） |

## V1.0 命名历史说明

本目录早期顶层 `README.md` 曾描述"V1.0 在 V0.2 基础上强制接入 RAG 知识库、禁用手工录入"。**该描述与代码事实不符**，已在本轮检查中废弃。事实如下：

- `ProjectRAGMap` / `SyncLog` / `SyncPending` / `FactsSnapshot` / `FactsRequestLog` 等模型
- `app/routers/rag_sync.py`（15 个 `/rag-sync/*` 路由）
- `app/services/facts_client.py`

以上 RAG 集成代码**均已在 V0.2 包中存在**（不是 V1.0 独有），所以"V1.0 强制 RAG 接入"的描述在代码层面不成立。

新的命名约定：

```
gtp_V1.0_FULL   ← V0.2 镜像快照（当前，本包）
gtp_V1.1_FULL   ← 下一个真正的功能增量版本（待启动）
gtp_V1.2_FULL   ← ...
gtp_V2.0_FULL   ← 重大架构升级
```

> 详细的快照与差异说明见 `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/CHANGELOG_V1.0.md`。

## 快速开始

> **首次使用**：系统要求登录。首个管理员账号由启动时的安全初始化流程创建；本资料包不提供内置账号密码。

在启动环境或 secret manager 中注入初始密码，不要把真实值写入本 README、`.env` 模板、日志或提交记录：

```bash
# 仅列出变量名；真实值必须由运行环境/secret manager 注入，本文不记录任何密码。
# INITIAL_ADMIN_PASSWORD
# INITIAL_OPERATOR_PASSWORD
```

`INITIAL_ADMIN_PASSWORD` 必须提供且满足密码强度策略；`INITIAL_OPERATOR_PASSWORD` 用于为操作员设置独立初始密码，建议始终提供。缺少必需变量或密码不合规时，系统应拒绝启动。

```bash
cd 01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0
./run_demo.sh
# 或
make demo

# 浏览器
open http://127.0.0.1:8921
```

## 测试

```bash
cd 01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0
make test
# 或
DATABASE_URL=sqlite:///./data/v01_test.db python -m pytest -q
```

## 文档

- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/README.md` —— V1.0 主系统 README（含快照性质说明）
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/CHANGELOG_V1.0.md` —— V1.0 快照说明
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/CHANGELOG_V02.md` —— V0.2 变更日志（V1.0 沿用）
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/ARCHITECTURE.md` —— 系统架构
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/V02_DESIGN.md` —— V0.2 设计说明
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/V02_TEST_REPORT.md` —— V0.2 测试报告
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/V02_MIGRATION.md` —— V0.1 → V0.2 迁移指南
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/AI_REVIEW_ARCHITECTURE.md` —— AI 编排架构
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/PRODUCTION_ROADMAP.md` —— 生产化路线图
