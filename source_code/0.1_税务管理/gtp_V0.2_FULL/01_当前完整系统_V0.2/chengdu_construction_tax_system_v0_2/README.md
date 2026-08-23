# 成都建筑施工企业综合经营与税务统筹系统 V0.2

V0.1 → V0.2 的全面升级：确定性引擎拆分、金额精度提升、安全合规强化、AI 编排并行化、整改闭环可视化。

## 主体编号（V0.2 已统一为 D）

### 系统内
- **A** 施工企业
- **B** 劳务公司
- **C** 商贸公司
- **D** 设备租赁公司

### 系统外
- **甲** 第三方劳务公司
- **乙** 第三方设备租赁公司
- **丙** 外部材料供应商
- **丁** 外部专业分包/其他供应商

## V0.2 主要改进

### 安全与合规（继承自 V0.1 优化建议）
- 全部金额字段 `Numeric(18, 2)`，杜绝浮点累加误差
- `AuditLog` 新增 `actor` / `ip`，满足"不可变审计"原则
- `sanitize_context` 强化 value 正则（身份证 / 手机号 / 银行卡）
- `Mock` 与 `openai_compatible` 适配器统一审计轨迹
- `parse_failed` 单独标记：模型返回非严格 JSON 时不再静默

### 业务正确性
- 风险阈值由 `RiskThreshold` 表驱动，可被业务侧调整
- CIT `note` 字段明示"未应用项清单"，杜绝"管理口径 → 正式申报"误用
- `note_category` 改为最长匹配 + 词边界，杜绝 `labor` 命中 `laboratory`
- `seed` 自适应当前月份，演示不再依赖硬编码 `2026-08`
- EAC 保留预算 / 当前效率 / 实际下限三档

### 性能与可扩展
- 健康体检 `Scope × Endpoint` 改**线程池并行**（不再串行阻塞 Web）
- `TTLCache` 提供 `endpoint_map` 与 `tax_ledger` 缓存
- `rebuild_tax_ledger` 改为单次全表扫描 + 内存聚合（O(N) 取代 O(4N) 查询）

### 架构
- `engine.py` 拆为 `calc/{project,matching,tax,risk,subjects}.py`
- `main.py` 拆为 `routers/{cockpit,matching,tax,ai_review,health_check,tasks,prompts,models,api}.py`
- `ai_review.py` 拆为 `ai/{context,prompt,adapter,mock,review,orchestrator,timeutil}.py`
- `constants.py` 唯一定义 `INTERNAL / EXTERNAL / SCOPES / HEALTH_PROFILES / TASK_STATUSES / RISK_ORDER`
- 全部金额 / 状态 / 枚举走 `frozenset` / `Literal`，避免拼写漂移

### 工程化
- `pyproject.toml`：`ruff` + `mypy` + `pytest` 配置
- `Makefile`：`make demo` / `make test` / `make fmt` / `make lint`
- `structlog` 结构化 JSON 日志，缺装时降级 `logging`
- `pytest` 化测试：原 `smoke.py` + `v01_flow.py` → `test_smoke.py` + `test_v02_flow.py`

## 快速启动

```bash
cd 01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2

# 一键启动
make demo
# 或
./run_demo.sh
# 或双击 run_demo_mac.command

# 浏览器
open http://127.0.0.1:8765
```

## 测试

```bash
make test
# 或
DATABASE_URL=sqlite:///./data/v02_test.db python -m pytest -q
```

## 核心页面

| 路由 | 功能 |
|---|---|
| `/` | 经营驾驶舱 |
| `/project/{id}` | 项目经营页 |
| `/matching` | 四流匹配 |
| `/tax-ledger` | ABCD 法人税务管理台账 |
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

## 重要边界

1. A / B / C / D 法律、会计和税务独立
2. 项目综合经营口径抵消 ABCD 内部交易 + 穿透真实底层成本
3. 甲乙丙丁 仅作为外部交易对手和外部成本变量
4. 税额、项目利润、四流、EAC 由确定性程序计算（金额 Numeric）
5. AI 只审查、解释、建议，**不得覆盖确定性结果**
6. Demo 税务规则默认 `reviewed=False`，**不可直接申报**
7. 风险阈值可由 `RiskThreshold` 表调整

## 文档

- `docs/ARCHITECTURE.md` —— 系统架构
- `docs/V02_DESIGN.md` —— V0.2 设计说明
- `docs/V02_TEST_REPORT.md` —— V0.2 测试报告
- `docs/V02_MIGRATION.md` —— V0.1 → V0.2 迁移指南
- `docs/AI_REVIEW_ARCHITECTURE.md` —— AI 编排架构
- `docs/PRODUCTION_ROADMAP.md` —— 生产化路线图