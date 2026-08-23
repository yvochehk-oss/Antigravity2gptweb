# V0.2 Changelog

> 历史说明：本文件记录 V0.2 时期的设计与测试。文中旧的单字主体/交易对手仅保留用于解释历史变更，不属于当前运行数据、接口或提示词；当前主体边界以 canonical Entity 与 ExternalParty 主数据为准。

## 安全与合规
- 金额字段全部 `Numeric(18, 2)`（project / invoice / real_cost / tax_ledger / ...）
- `AuditLog.actor` / `AuditLog.ip` 字段上线
- `sanitize_context` 增加 value 正则（身份证 / 手机号 / 银行卡）
- `Mock` 适配器与真实 HTTP 统一审计轨迹
- 模型返回非 JSON 时单独标记 `parse_failed`，UI 红色 banner 提示

## 业务正确性
- `RiskThreshold` 表驱动风险阈值，可由 `/risks` 调整
- `TaxLedger.cit_note` 明示"未应用项清单"
- `note_category` 改为最长匹配 + 词边界（修复 `labor` 命中 `laboratory`）
- `seed` 自适应当前月份
- EAC 保留三档：预算 / 当前效率 / 实际下限
- 现金流无法识别类别时不再"自动归类"，保留 `unclassified` 并在状态列显式标记

## 性能
- 健康体检改 `ThreadPoolExecutor` 并行
- `TTLCache` 提供 `endpoint_map` 与 `tax_ledger` 缓存
- `rebuild_tax_ledger` 单次全表扫描 + 内存聚合
- `audit` 操作统一进 `audit()` 辅助函数

## 架构
- `engine.py` 拆为 `calc/{project,matching,tax,risk,subjects}.py`
- `main.py` 拆为 `routers/{cockpit,matching,tax,ai_review,health_check,tasks,prompts,models,api}.py`
- `ai_review.py` 拆为 `ai/{context,prompt,adapter,mock,review,orchestrator,timeutil}.py`
- `constants.py` 唯一定义 `BUSINESS_ROLES / ENTITY_ROLES`、canonical 主数据代码、`SCOPES / HEALTH_PROFILES / TASK_STATUSES / RISK_ORDER / DEFAULT_RISK_THRESHOLDS`
- `Pydantic` 契约：`Finding / Recommendation / AIReviewPayload / HealthCheckRequest / TaskUpdateRequest`

## 工程化
- `pyproject.toml`：`ruff` + `mypy` + `pytest`
- `Makefile`：`make demo / seed / run / test / fmt / lint / clean`
- `structlog` 结构化日志（缺装时降级 `logging`）
- `pytest` 化：`tests/conftest.py + test_smoke.py + test_v02_flow.py + test_calc.py + test_sanitize.py`
- 启动脚本双 fallback `python3 / python`
- 历史设计文档中的业务角色 `E → D` 统一（不影响当前 canonical 法人代码）

## 文档更新
- `02_总体设计与指导文件/...docx` 的历史业务角色编号改为 D
- `01_当前完整系统_V0.2/docs/V02_DESIGN.md` 新增
- `01_当前完整系统_V0.2/docs/V02_TEST_REPORT.md` 新增
- `01_当前完整系统_V0.2/docs/V02_MIGRATION.md` 新增
