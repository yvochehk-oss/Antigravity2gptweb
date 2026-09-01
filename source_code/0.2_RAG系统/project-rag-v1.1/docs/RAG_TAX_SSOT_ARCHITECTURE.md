# RAG → Tax 单一事实源（SSOT）架构白皮书

## 1. 架构裁决

当前 RAG（8922）与 Tax（8921）已经连接同一个 PostgreSQL `projectrag`，并且 `projects`、`entities`、`external_parties` 等主数据表已经物理共享。因此目标架构不是“两个数据库之间复制同步”，而是：

> **一个数据库、一个 Canonical Facts 写入边界、多个只读消费者。**

RAG 负责把合同、发票、付款凭证等非结构化证据转换为带来源、版本、校验状态的规范事实；Tax 不再拥有第二份抽取事实，也不再把 HTTP `/extract-tax` + `sync_pending` 当作主数据总线。Tax 的职责限定为确定性计算、台账展示、穿透合并、税负测算和筹划。

这与既定三层事实原则一致：

1. Documentary Truth：原始合同、发票、银行回单、完税凭证及其页码/文本证据；
2. Structured Truth：`canonical_facts` + 规范主体主数据；
3. Deterministic Calculation：Tax 依据只读事实执行会计/税务公式，不调用 LLM 重算事实。

## 2. 为什么 RAG 应成为 Tax 的唯一事实来源

旧链路为：

`RAG documents -> HTTP extract-tax -> Tax sync_pending -> 人工确认 -> Tax contracts/invoices/cashflows`

每一个箭头都可能产生字段漂移、名称漂移、重试重复、409、局部成功和审计断链。特别是同一主体同时出现 canonical code、税号、公章全称、简称时，Tax 被迫重复做一遍 RAG 已完成的身份解析。

新链路为：

`Document -> RAG parse/extract -> canonical_facts -> analytics_canonical_facts_current -> Tax deterministic engine`

Tax 只消费 canonical code 和已接受事实，不再复制“待解释的 OCR 结果”。每条事实携带 `source_document_id / fact_type / business_key / fact_version / source_hash / payload / evidence / validation_errors / producer / accepted_at`，因此 Tax 数字可以逐层反查到事实版本与原始文档。

## 3. 同库方案选型

### 3.1 主方案：只读事实视图 / 共享模型直通

这是默认方案：

- RAG 是 `canonical_facts` 唯一业务写入者；
- Tax 读取 `analytics_canonical_facts_current`；
- Tax 不 UPDATE/DELETE canonical facts；
- `needs_review` 不进入计算视图；
- 新版本 accepted 后，旧 accepted 版本变为 `superseded`；
- 同一来源哈希重复处理保持幂等。

优点是无复制延迟、无跨库一致性问题、无字段漂移、无重复主数据。

### 3.2 辅助方案：事务 Outbox

`canonical_fact_outbox` 与 accepted fact 在同一事务写入，只承担缓存刷新、昂贵报表重算、前端通知和未来跨进程事件传递。Outbox 不是第二份事实库，消费者永远可以回到 canonical view 重放。

### 3.3 不采用 Trigger 执行业务计算

数据库 Trigger 只适合约束和极轻量事件，不承载利润、增值税、所得税、成本穿透等业务规则。税务规则必须留在可测试、可版本化的确定性 Python/SQL 计算层。

## 4. 自动事实提升

RAG ingest worker 在 `parse_and_index()` 成功、Document 状态变为 `INDEXED` 后自动执行 canonical fact promotion。第一阶段自动支持 contract、invoice、payment：

1. 聚合同一 Document 的 Chunk 文本；
2. 使用现有确定性 extractor；
3. 用 Document 已登记的 `entity_code / counterparty_code / contract_no` 回填身份；
4. 外部别名持久化前归一化为 canonical E* code；
5. 执行主体、金额、日期、发票算术等确定性断言；
6. 通过 -> `accepted/current`；
7. 未通过 -> `needs_review`，不进入 Tax 计算视图。

因此“人工复核”从正常流程降级为异常队列，而不是所有合同/发票都必须点击一次。

## 5. sync_pending 的平滑退出

迁移期间保留 `sync_pending` API 与界面作为兼容/异常通道，但新 canonical facts 正常流不依赖它。前端后续只展示 `analytics_canonical_fact_review_queue`。达到以下门槛后可删除“确认创建交易方并导入合同”正常按钮：

- accepted 自动入账率 >= 99.5%；
- canonical identity unresolved < 0.1%；
- 连续 30 天无重复入账；
- canonical/current 与 legacy ledger 对账差异 = 0。

## 6. 系统内外穿透计算

主体节点分为系统内 26 个法人代码和系统外 E0/EA/EB/EC/ED 等 canonical external parties。交易边按 `seller -> buyer` 建模，每条边保留 project、fact id/version、category、net、VAT、deductible、period 和 evidence lineage。

同一项目的合并规则：

- internal -> internal：内部收入与内部成本同时抵消；
- external -> internal：系统真实外部发生成本；
- internal -> external：系统真实外部收入；
- external -> external：不属于系统经营成果。

例如 `EB(钢材商) -> A08 -> B03 -> C01`，外采净额 1000 万，A08 转售 B03 1100 万、B03 再传递 C01 1200 万，则系统合并口径真实外部来源成本仍为 1000 万，1100 万和 1200 万全部作为内部流水消除。

VAT 与利润必须分轨：可抵扣进项 VAT 不进入真实成本；不可抵扣 VAT 才进入成本；payment facts 只用于资金流和已付/应付匹配，不能与 invoice 成本重复计入 P&L。

本阶段 `canonical_ssot.build_consolidated_project_pnl()` 先对 accepted invoice facts 完成 `external_revenue / external_cost / internal_eliminated / true_profit` 边界合并。后续履约、结算、未票成本、税会差异继续叠加在同一 lineage 上，而不是改变事实源。

## 7. 幂等、版本与审计

- `source_document_id + fact_type + source_hash` 唯一，重复 worker/重启不会制造第二笔事实；
- 同一 `project_id + fact_type + business_key` 只能有一条 accepted/current；
- 新事实 accepted 时旧版本 supersede，不物理覆盖；
- 主体无法归一化、身份冲突、发票算术不成立、必填日期/金额缺失时 fail closed；
- `source_hash`、版本、producer、evidence、validation_errors 全部保留；
- Tax 计算结果还应继续记录 calculation algorithm version，从而区分“事实版本”和“算法版本”。

## 8. 实施路径

### Phase 1（本提交）
- 建立 `canonical_facts`、current/review 视图与 outbox；
- RAG parse worker 自动 promotion；
- 提供历史 indexed Documents backfill；
- Tax 新增 direct SSOT reader 与 consolidated P&L endpoint；
- 保留旧 sync_pending 作为兼容路径。

### Phase 2
- 全量回填历史 Documents 并完成 legacy 对账；
- Tax 合同/发票/付款列表逐页切换到 canonical views；
- legacy 写表改为只读兼容 projection；
- 禁止 UI 正常路径二次创建 canonical external party。

### Phase 3
- 删除正常路径 HTTP `/extract-tax`；
- 删除“确认创建交易方并导入合同”正常按钮；
- review UI 只处理 `needs_review`；
- outbox 只通知刷新，不复制事实。

### Phase 4
- `analytics_*` 全部从 canonical facts / deterministic ledger 派生；
- AI Review 只读 Canonical JSON + RAG evidence；
- 建立事实版本、算法版本、报表版本三重 lineage。

## 9. 权限边界

生产建议分离数据库角色：

- `projectrag_writer`：RAG 可写 documents/chunks/canonical_facts；
- `tax_engine`：canonical facts 仅 SELECT，Tax calculation ledger 可写；
- `report_reader`：仅 SELECT analytics views。

即使当前同进程使用同一 DATABASE_URL，代码层也先遵守该边界，后续再收紧 PostgreSQL GRANT。

## 10. 最终结论

“Tax 只能以 RAG 为单一数据来源”在本项目中成立，但准确表述应为：

> **Tax 以 RAG 产出的 Canonical Facts 为唯一业务事实来源，而不是直接把原始文档/OCR/LLM 输出当作事实。**

RAG 管证据与事实，Tax 管算法与结果；同库读直通是主路径，事务事件只做唤醒。正常凭证自动进入确定性台账，人工只处理异常。这是当前物理条件下复杂度最低、审计最强、长期维护成本最低的目标架构。
