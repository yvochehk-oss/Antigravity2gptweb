# V1.0 完整资料包说明

> **本包性质**：V1.0 是 **V0.2 的镜像快照副本**，代码与 `gtp_V0.2_FULL/01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/` **逐字节一致**。
>
> 本资料包说明文档沿用 V0.2 资料包说明的内容，但**所有路径已切换到 V1.0 包内**。

## 资料包结构

```
gtp_V1.0_FULL/
├── 01_当前完整系统_V1.0/
│   └── chengdu_construction_tax_system_v1_0/   # 可运行代码（V0.2 镜像快照）
├── 02_总体设计与指导文件/                         # 总体设计 Word 文档
├── 03_历史版本归档/                              # V0.1 副本对照（保留）
├── README.md                                    # 顶层说明
├── README_完整资料包说明.md                      # 资料包说明（本文件）
└── FILE_MANIFEST.txt                            # 文件清单
```

## V0.2 核心改进（V1.0 沿用）

1. **安全合规**：金额 Numeric(18, 2)，审计 actor/ip，脱敏 value 正则，Mock 审计一致
2. **业务正确性**：风险阈值表化，CIT note，词边界修复，主体编号 E→D
3. **RAG 集成**：`ProjectRAGMap` / `SyncLog` / `SyncPending` / `FactsSnapshot` / `FactsRequestLog`，15 个 `/rag-sync/*` 路由
4. **性能**：健康体检线程池并行，TTLCache，SQL 聚合优化
5. **架构**：engine.py 拆 calc/，main.py 拆 routers/，ai/ 子包
6. **工程化**：pytest + ruff + mypy + Makefile，结构化日志

## V1.0 与 V0.2 包的关系

| 项 | 内容 |
|---|---|
| V1.0 路径 | `gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/` |
| V0.2 路径 | `gtp_V0.2_FULL/01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/` |
| 代码差异 | **0**（逐字节一致） |
| 路由数 | **55**（与 V0.2 完全一致） |
| 测试数 | **5**（与 V0.2 完全一致） |

## 快速开始

```bash
cd 01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0
./run_demo.sh
# 或：
make demo
```

## 测试

```bash
cd 01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0
make test
# 或：
DATABASE_URL=sqlite:///./data/v01_test.db python -m pytest -q
```

当前状态：**测试与 V0.2 包完全一致**（运行 `make test` 验证）。

## 文档

- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/README.md`
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/CHANGELOG_V1.0.md`
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/CHANGELOG_V02.md`
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/V02_DESIGN.md`
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/V02_TEST_REPORT.md`
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/V02_MIGRATION.md`
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/ARCHITECTURE.md`
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/AI_REVIEW_ARCHITECTURE.md`
- `01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/docs/PRODUCTION_ROADMAP.md`

## V0.1 升级对照

详细迁移指南：`docs/V02_MIGRATION.md`。

主要差异（V0.1 → V0.2/V1.0）：
- 字段从 `Float` 改为 `Numeric(18, 2)`
- 新增 `RiskThreshold` / `parse_failed` / `actor` / `ip`
- 路由全部位于 `app/routers/`
- AI 全部位于 `app/ai/`
- 文档主体编号 E 改为 D（设备租赁公司）
- 新增 RAG 集成模型与 `/rag-sync/*` 路由

## 文件清单

见 `FILE_MANIFEST.txt`。