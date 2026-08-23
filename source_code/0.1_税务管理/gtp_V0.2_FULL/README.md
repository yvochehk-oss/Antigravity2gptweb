# V0.2 完整资料包

本目录为成都建筑施工企业综合经营与税务统筹系统 V0.2 完整资料包。

## 目录结构

```
gtp_V0.2_FULL/
├── 01_当前完整系统_V0.2/
│   └── chengdu_construction_tax_system_v0_2/   # 可运行代码
├── 02_总体设计与指导文件/                         # 总体设计word文档
└── 03_历史版本归档/                              # V0.1 副本对照（保留）
```

## V0.2 主要变更

- 金额精度：Float → Numeric(18, 2)
- 审计：增加 actor / ip
- 风险阈值：硬编码 → `RiskThreshold` 表
- 健康体检：串行 → ThreadPool 并行
- 整改闭环：AI 复检自动建 AI ReviewJob
- 架构：`engine.py` 拆 `calc/`，`main.py` 拆 `routers/`
- 工程化：pytest + ruff + mypy + Makefile
- 文档：主体编号 E → D

## 快速开始

```bash
cd 01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2
./run_demo.sh
```

## 测试

```bash
cd 01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2
DATABASE_URL=sqlite:///./data/v02_test.db python -m pytest -q
```

## 文档

- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/README.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/CHANGELOG_V02.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/docs/V02_DESIGN.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/docs/V02_TEST_REPORT.md`
- `01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/docs/V02_MIGRATION.md`