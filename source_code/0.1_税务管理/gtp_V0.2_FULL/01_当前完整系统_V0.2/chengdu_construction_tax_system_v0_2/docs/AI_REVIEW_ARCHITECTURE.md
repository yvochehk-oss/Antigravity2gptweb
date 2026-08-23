# AI 审查架构（V0.2）

## 1. 角色边界

| 角色 | 能做 | 不能做 |
|---|---|---|
| 确定性引擎（calc/） | 算利润 / 算税额 / 算 EAC / 算四流匹配 / 算风险 | 解释业务含义 / 给整改建议 |
| AI 审查（ai/） | 解释 / 审查 / 建议 / 提示数据缺口 | 覆盖确定性数字 / 创造性变更 / 给出不能溯源的建议 |

**强制约束**：系统提示词 (ai/prompt.py) 在 "强制边界" 段落中明确列出。

## 2. 上下文构建

`build_context(db, project_id, scope)` 按 scope 抽取最小数据：

```python
{
  "project": {...},
  "system_calculation": {..."eac": ..., "risk": ...},
  "scope": "tax",
  "scope_name": "税务",
  "tax_ledgers": [...],
  "tax_rule_review_status": [...],
  ...
}
```

`whole_project` scope 截断 + 显式摘要（避免静默丢数）。

## 3. Prompt 模板

`AIPromptTemplate` 字段：
- `name` / `scope` / `version`
- `system_addendum`（在系统提示词后追加）
- `review_focus`（审查重点）
- `enabled`（启用状态）

每次检查写入 `job.prompt_template_id`，与 `version` 一起保证可追溯。

## 4. 适配器

```python
class AIModelEndpoint:
    adapter: "mock" | "openai_compatible"
    base_url: str
    chat_path: str
    model: str
    api_key_env: str  # 只存环境变量名
    timeout_seconds: int
```

API Key 仅存环境变量名，**不入库**。

## 5. 共识

`build_consensus` 本地确定性汇总，不调用模型：

- 多模型共同发现 → `common_findings_json`
- 多模型差异意见 → `differences_json`
- 共识 `overall_risk` 取最高等级
- 共识 `score` 取平均

## 6. 整改闭环

```
AI Review → 用户确认 → RemediationTask → 整改 → 复检 → 用户确认 → 关闭
```

- `parent_job_id` = 触发整改任务的原始 AIReviewJob
- `recheck_job_id` = 复检创建的 AIReviewJob
- AI 不得直接 verified/closed，必须人确认

## 7. 审计

- `AI_REVIEW_START` / `AI_REVIEW_COMPLETE` / `AI_REVIEW_FAILED`
- `AI_HEALTH_START` / `AI_HEALTH_COMPLETE` / `AI_HEALTH_FAILED`
- `AI_RECHECK` / `AI_RECHECK_FAILED`
- 全部审计记录写入 `AuditLog`，含 actor/ip/message

## 8. 安全红线

1. AI 不得提供"虚构劳务 / 虚构采购 / 虚假设备租赁 / 购买发票 / 资金空转 / 倒签合同"建议
2. AI 不得为未复核税务规则背书
3. AI 不得在 `data_gaps` 缺失时仍给出可执行结论
4. AI 返回非 JSON 时必须 `parse_failed=true`，UI 标记，需人工审阅


## 9. 实际部署建议

- 默认走 `mock` 适配器便于回归测试
- 真实生产环境从 `openai_compatible` 适配器接入
- 失败重试：当前不内置重试，由 `run_health_check` 收集失败到 `batch.error_message`
- 限流：外部 endpoint 调用建议增加 RateLimitProxy（V0.2 未实现）