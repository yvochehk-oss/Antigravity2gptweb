# V0.2 完整资料包说明

本目录为成都建筑施工企业综合经营与税务统筹系统 V0.2 完整资料包。

## 资料包结构

```
gtp_V0.2_FULL/
├── 01_当前完整系统_V0.2/
│   └── chengdu_construction_tax_system_v0_2/   # 可运行代码
├── 02_总体设计与指导文件/                         # 总体设计 Word 文档
├── 03_历史版本归档/                              # V0.1 副本对照（保留）
├── README.md                                    # 顶层说明
└── FILE_MANIFEST.txt                            # 文件清单
```

## V0.2 核心改进

1. **安全合规**：金额 Numeric(18, 2)，审计 actor/ip，脱敏 value 正则，Mock 审计一致
2. **业务正确性**：风险阈值表化，CIT note，词边界修复，主体编号 E→D
3. **性能**：健康体检线程池并行，TTLCache，SQL 聚合优化
4. **架构**：engine.py 拆 calc/，main.py 拆 routers/，ai/ 子包
5. **工程化**：pytest + ruff + mypy + Makefile，结构化日志

## 快速开始

```bash
cd 01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2
./run_demo.sh
# 或：
make demo
```

## 测试

```bash
cd 01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2
make test
# 或：
DATABASE_URL=sqlite:///./data/v02_test.db python -m pytest -q
```

当前状态：**13 passed in 1.17s**。

## 文档

- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/README.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/CHANGELOG_V02.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/docs/V02_DESIGN.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/docs/V02_TEST_REPORT.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/docs/V02_MIGRATION.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/docs/ARCHITECTURE.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/docs/AI_REVIEW_ARCHITECTURE.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/docs/PRODUCTION_ROADMAP.md`

## V0.1 升级对照

详细迁移指南：`docs/V02_MIGRATION.md`。

主要差异：
- 字段从 `Float` 改为 `Numeric(18, 2)`
- 新增 `RiskThreshold` / `parse_failed` / `actor` / `ip`
- 路由全部位于 `app/routers/`
- AI 全部位于 `app/ai/`
- 文档主体编号 E 改为 D（设备租赁公司）

## 文件清单

见 `FILE_MANIFEST.txt`。