# V0.2 设计说明

## 1. 设计目标

V0.1 在 AI 编排层（多模型交叉 / 整改闭环）已经成型，但底层仍存在：
- 金额精度隐患（Float 累加误差）
- 关键审计字段缺失（actor / ip）
- 风险阈值硬编码
- 体检串行阻塞 Web
- "engine.py" 700+ 行单文件
- 测试贴近脚本（`smoke.py` / `v01_flow.py`），无 pytest 框架

V0.2 在不破坏 V0.1 业务语义的前提下完成全面重构。

## 2. 目录结构

```
chengdu_construction_tax_system_v0_2/
├── app/
│   ├── constants.py          # 唯一常量定义
│   ├── db.py                 # SQLAlchemy 引擎 + Base
│   ├── models.py             # 全部 ORM 模型（金额 Numeric）
│   ├── audit.py              # 审计 actor/ip + audit()
│   ├── sanitize.py           # 强化脱敏（key + value）
│   ├── cache.py              # TTLCache
│   ├── structured_logging.py # structlog 配置
│   ├── schemas.py            # Pydantic 契约
│   ├── templates.py          # Jinja2Templates 单例
│   ├── seed.py               # 演示数据（自适应月份 + RiskThreshold）
│   ├── calc/                 # 纯函数计算引擎
│   │   ├── project.py        # project / consolidated / cost_tree
│   │   ├── matching.py       # 四流匹配 + 风险阈值
│   │   ├── tax.py            # 法人月度税务台账
│   │   ├── risk.py           # 风险事件
│   │   └── subjects.py       # 统一科目映射
│   ├── ai/                   # AI 编排
│   │   ├── context.py        # 最小结构化上下文
│   │   ├── prompt.py         # 模板 + 系统提示
│   │   ├── adapter.py        # mock / openai_compatible
│   │   ├── mock.py           # mock 审查器
│   │   ├── review.py         # 单次 AI 检查
│   │   ├── orchestrator.py   # 健康体检 + 共识 + 复检
│   │   └── timeutil.py
│   ├── routers/              # 路由（按职责拆）
│   │   ├── cockpit.py        # 驾驶舱 / 主数据 / 导入
│   │   ├── matching.py
│   │   ├── tax.py
│   │   ├── ai_review.py
│   │   ├── health_check.py
│   │   ├── tasks.py
│   │   ├── prompts.py
│   │   ├── models.py
│   │   └── api.py
│   ├── main.py               # 入口
│   └── templates/            # Jinja2 模板
├── data/
│   └── templates/            # CSV 模板
├── skills/construction-tax-cn/SKILL.md
├── tests/
│   ├── conftest.py
│   ├── test_smoke.py
│   ├── test_calc.py
│   ├── test_sanitize.py
│   └── test_v02_flow.py
├── docs/
│   ├── V02_DESIGN.md         # 本文件
│   ├── V02_TEST_REPORT.md
│   ├── V02_MIGRATION.md
│   ├── ARCHITECTURE.md
│   ├── AI_REVIEW_ARCHITECTURE.md
│   └── PRODUCTION_ROADMAP.md
├── pyproject.toml
├── requirements.txt
├── Makefile
├── run_demo.sh
├── run_demo_mac.command
├── README.md
└── CHANGELOG_V02.md
```

## 3. 关键设计决策

### 3.1 金额 Numeric

所有金额字段（contract / invoice / real_cost / tax_ledger / cashflow / progress / budget / fulfillment）改为 `Numeric(18, 2)`。

- 序列化时统一 `float()` 转为 JSON 数字
- 模板渲染 `%.0f` 格式避免 `Decimal('23...')` 直接显示
- `json.dumps` 使用 `default=str` 兜底

### 3.2 风险阈值表化

```python
class RiskThreshold(Base):
    code: str        # invoice_over_contract / paid_over_invoice / fulfilled_over_contract
    ratio: Decimal
    severity: str
    enabled: bool
```

`matching_rows` 优先查表，查不到回退 `DEFAULT_RISK_THRESHOLDS`。业务侧可调整而无需改代码。

### 3.3 健康体检并行化

```python
with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as pool:
    futures = {
        pool.submit(_single_run, batch_id, scope, eid, instruction): (scope, eid)
        for scope, eid in tasks
    }
    for fut in as_completed(futures): ...
```

- 每个 scope × endpoint 独立 SessionLocal
- 失败被捕获聚合到 `batch.error_message`，不影响其他子任务
- 多模型共同发现：`confirmed_by = set(models)`

### 3.4 安全管理

- `sanitize_context`：同时匹配 key 关键词与 value 正则
- `audit()` / `audit_from_request()`：actor/ip 强制
- `Mock` 适配器与 `openai_compatible` 统一审计轨迹
- `parse_failed` 字段单独标记非 JSON 响应

### 3.5 整改闭环

```
用户 → AI 检查 → 任务创建 → 整改 → 复检 → 用户确认 → 关闭
```

- `RemediationTask` 状态机：`open → in_progress → done → rechecked → verified → closed`
- AI 复检自动写 `recheck_job_id` + `parent_job_id`
- AI 不能直接修改 `status`，必须由人确认

### 3.6 常量唯一化

`constants.py` 唯一定义：
- `INTERNAL = frozenset({"A", "B", "C", "D"})`
- `EXTERNAL = frozenset({"甲", "乙", "丙", "丁"})`
- `SCOPES` / `HEALTH_PROFILES` / `TASK_STATUSES` / `RISK_ORDER`
- `ENTITY_ROLES` / `KIND_TO_CATEGORY` / `CATEGORY_SCOPE`
- `DEFAULT_RISK_THRESHOLDS`

业务规则变更只改一处。

## 4. 已确认的边界

- `note_category` 改用 ASCII 词边界正则（修复 `labor` 命中 `laboratory`）
- CIT 备注明示"未应用项清单"，避免管理口径误用于正式申报
- 现金流无法识别类别时保留 `unclassified` 并在状态列显式标记
- 体检维度保留 `quick / standard / deep` 三档
- 模板版本变更创建新版本 + 旧版保留，便于审计追溯

## 5. 与 V0.1 的兼容性

- 数据模型向后兼容：所有 V0.1 字段 + V0.2 新增字段
- API 路径保持不变
- 模板可平移（V0.1 模板直接换上下文变量名可用）
- skills/construction-tax-cn/SKILL.md 已更新 V0.2 流程

详细迁移指南见 `docs/V02_MIGRATION.md`。