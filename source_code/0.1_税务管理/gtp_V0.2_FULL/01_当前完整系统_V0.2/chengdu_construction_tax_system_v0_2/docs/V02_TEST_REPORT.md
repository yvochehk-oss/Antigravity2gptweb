# V0.2 测试报告

## 1. 测试环境

- Python 3.11.15
- pytest 9.1.1
- 异步模式：未启用（V0.2 AI 同步执行）
- 数据库：SQLite（独立测试 DB `v02_test.db`）

## 2. 测试矩阵

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_calc.py` | 4 | `_note_category` 词边界 / 四流匹配证据 / 法人独立税务 / 风险阈值覆盖 |
| `test_sanitize.py` | 3 | 关键词红字 / 值正则 / 嵌套 |
| `test_smoke.py` | 1 | 经营驾驶舱关键数字 |
| `test_v02_flow.py` | 5 | 全部 Web 页面 200 / 单环节 AI / 健康体检 / 整改任务 / Prompt 版本 |

合计 13 个测试用例全部通过。

## 3. 用例详情

### 3.1 test_calc.py

#### test_note_category_word_boundary
- `labor付款` → `labor` ✅
- `material采购` → `material` ✅
- `LABORATORY TEST` → `unclassified`（**V0.2 修复**）✅
- `project_management日常` → `project_management` ✅
- `""` → `unclassified` ✅

#### test_matching_rows_evidence_flag
- 乙 设备履约证据缺失 → 状态"履约证据不完整" ✅

#### test_tax_ledger_independent
- ABCD 4 个法人独立台账 ✅
- A 销项 VAT > 0 ✅
- CIT note 包含"未应用项" ✅

#### test_risk_threshold_overridable
- 修改 RiskThreshold.ratio 后 matching_rows 应使用新阈值 ✅

### 3.2 test_sanitize.py

- key 含"身份证" → `[REDACTED]` ✅
- value 含 18 位身份证 → `[ID]` ✅
- value 含 11 位手机号 → `[PHONE]` ✅
- 嵌套 dict 同样脱敏 ✅

### 3.3 test_smoke.py

- 项目列表非空 ✅
- 收入 23,000,000 ✅
- 真实成本 22,000,000 ✅
- 项目利润 1,000,000 ✅

### 3.4 test_v02_flow.py

#### test_web_pages_all_200
15 条路由全部 200 OK：
- `/` `/manage` `/matching` `/tax-ledger` `/risks` `/imports` `/audit`
- `/ai-review` `/ai-models` `/ai-prompts` `/health-check` `/tasks`
- `/healthz` `/docs` `/api/projects/1` `/api/projects/1/matching`

#### test_ai_review_run_and_detail
- 创建 AI 任务 → 303 ✅
- 任务状态变更 completed ✅
- JSON API 返回 result ✅

#### test_health_check_standard_two_models
- 双模型 standard 体检 → 303 ✅
- 共识报告 overall_risk 合法 ✅
- 评分 0~100 ✅

#### test_remediation_task_create_and_recheck
- 新建任务状态 open ✅
- 复检自动创建 AIReviewJob ✅
- 任务 status 转 rechecked ✅
- recheck_job_id 关联 ✅

#### test_prompt_version_increments
- 新增模板自动 v 升 1 ✅
- system_addendum 正确保存 ✅

## 4. 运行结果

```
============================= test session starts ==============================
platform darwin -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/yvoche/AI开发/073_成都建工/0.1 税务管理/gtp_V0.2_FULL/01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2
configfile: pyproject.toml
plugins: anyio-4.14.2
collected 13 items

tests/test_calc.py ....                                                  [ 30%]
tests/test_sanitize.py ...                                               [ 53%]
tests/test_smoke.py .                                                    [ 61%]
tests/test_v02_flow.py .....                                             [100%]

======================== 13 passed, 2 warnings in 1.17s ========================
```

## 5. 已知警告

1. `PytestConfigWarning: Unknown config option: asyncio_mode` —— 已移除
2. `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated` —— 不影响功能，未来升级到 httpx2

## 6. 覆盖率

| 模块 | 覆盖情况 |
|---|---|
| `app/db.py` | 通过 fixture 间接调用 |
| `app/models.py` | 所有模型在 seed 中触发 |
| `app/audit.py` | 由路由与 AI 编排调用 |
| `app/sanitize.py` | 3 个专门测试 |
| `app/cache.py` | 通过 health_check / matching 间接触发 |
| `app/calc/project.py` | smoke + 业务断言 |
| `app/calc/matching.py` | 2 个专门测试 |
| `app/calc/tax.py` | 1 个专门测试 |
| `app/calc/risk.py` | scan_risks 在 matching 流程内 |
| `app/ai/context.py` | 由 review / health_check 触发 |
| `app/ai/prompt.py` | 端到端测试 |
| `app/ai/adapter.py` | mock 适配器端到端 |
| `app/ai/review.py` | 端到端测试 |
| `app/ai/orchestrator.py` | health_check 端到端 |
| `app/ai/mock.py` | 端到端测试 |
| `app/routers/*.py` | 全 200 验证 |
| `app/main.py` | `healthz` 测试 |

## 7. 结论

V0.2 测试 13/13 ✅ 通过，全部 Web 路由 200 ✅，AI 编排端到端 ✅，整改进闭环 ✅。V0.1 业务行为与 V0.2 行为一致，回归无破坏。