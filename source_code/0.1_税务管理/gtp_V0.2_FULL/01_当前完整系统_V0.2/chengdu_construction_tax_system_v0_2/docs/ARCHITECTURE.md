# V0.2 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         Web UI (Jinja2)                         │
│  /  /manage  /matching  /tax-ledger  /risks  /imports  /audit  │
│  /ai-review  /health-check  /tasks  /ai-prompts  /ai-models   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       FastAPI Routers (V0.2)                    │
│  cockpit / matching / tax / ai_review / health_check / tasks    │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌──────────────┐    ┌────────────────────┐    ┌──────────────┐
│   calc/      │    │       ai/          │    │   audit/     │
│  project     │    │  context / prompt  │    │   sanitize   │
│  matching    │    │  adapter / mock    │    │   cache      │
│  tax         │    │  review / orchestr │    │              │
│  risk        │    │  timeutil          │    │              │
└──────────────┘    └────────────────────┘    └──────────────┘
        │                     │                     │
        ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│           SQLAlchemy ORM (Numeric/Decimal 安全)                  │
│  Project / Contract / Invoice / CashFlow / Fulfillment / RealCost│
│  TaxLedger / TaxRule / RiskEvent / RiskThreshold                │
│  AIPromptTemplate / AIModelEndpoint / AIReviewJob / Batch       │
│  AIConsensusReport / RemediationTask / AuditLog                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              SQLite (single file) / 可换 PostgreSQL              │
└─────────────────────────────────────────────────────────────────┘
```

## 安全边界

- **AI 只做审查、解释、建议**，不得修改确定性结果（利润 / 税额 / EAC）
- 金额字段全部 `Numeric(18, 2)`，避免浮点累加
- `audit` 操作强制 `actor + ip`
- `sanitize` 同时匹配 key 关键词 + value 正则
- Mock 适配器与真实 HTTP 统一审计轨迹
- 税务规则 `reviewed=False` 时 AI 不得为正式申报背书

## 性能边界

- 健康体检 `Scope × Endpoint` 改 ThreadPool 并行（max ≈ 8）
- `TTLCache` 缓存 `endpoint_map` 与 `tax_ledger`
- `rebuild_tax_ledger` 单次 GROUP BY + 内存聚合
- `recheck_task` 复用现有 AI 路径，保留 `parent_job_id` / `recheck_job_id`
- Demo 规模（< 100 项目，< 1000 docs）下整体响应 < 200ms

## 扩展边界

- 接入新模型：新建 `AIModelEndpoint`（adapter=openai_compatible）
- 接入新审查范围：新增 `SCOPES` enum + `build_context` 分支 + Prompt 模板
- 接入新审计规则：新增 `RiskThreshold` 行
- 接入新整改流程：新建 `RemediationTask` + 调 `/tasks/{id}/recheck`