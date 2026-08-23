# 生产化路线图（V0.2 之后）

## V0.2 内已实现

- 金额 Numeric(18, 2)
- 审计 actor + ip
- 风险阈值表化
- 健康体检并行
- 整改闭环
- pytest + ruff + mypy + Makefile
- Duck typing 的枚举常量集中在 `constants.py`

## V2.6 候选

| 优先级 | 项 | 描述 |
|---|---|---|
| P0 | 用户认证与授权 | RBAC、登录、session、CSRF、生产密钥管理 |
| P0 | 余额锁 / 乐观锁 | 多用户编辑同一 row 时的并发保护 |
| P0 | 导出审计 | 防篡改导出（CSV + 哈希指纹） |
| P1 | 异步任务队列 | 把 AI 改 Celery / RQ / Dramatiq，前端轮询 |
| P1 | 实时通知 | SSE / WebSocket 把体检进度推回前端 |
| P1 | OpenTelemetry | 追踪 / 指标 / 日志统一 |
| P1 | PostgreSQL 适配 | 用 JSONB 存 findings，事务隔离提升 |
| P2 | 多租户 | 支持多个独立公司 |
| P2 | 移动端 API | OpenAPI + JWT |
| P2 | 离线模式 | 本地 SQLite + 周期性同步 |
| P2 | 国际化 | 中英双语 |

## V0.2 已知限制

1. 演示版 actor 全部为 `"demo-user"`，未接用户系统
2. 实时进度未推送（改 `/health-check/{id}` 刷新）
3. 重试 / 限流 由调用方负责
4. 报告导出格式仅 HTML
5. 没有集成电子签章 / OCR 解析发票
6. 没有与企业微信 / 钉钉 / 飞书集成

## 治理建议

- 所有税务规则必须由具备资质的财税人员复核后才能置 `reviewed=true`
- AI 建议必须由"项目财务 / 商务"双签后才能进业务流
- 重大事项（CIT 申报、年度汇算）必须暂停 AI 使用
- 审计日志建议按月归档加密存储