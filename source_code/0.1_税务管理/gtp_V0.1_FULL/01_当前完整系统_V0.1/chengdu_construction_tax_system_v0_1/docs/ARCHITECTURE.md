# 系统完整代码设计

## 核心分层
Web UI → FastAPI业务服务 → 项目经营引擎 / 税务引擎 / EAC引擎 / 风险引擎 → PostgreSQL。AI Skill只读取确定性结果。

## 核心边界
- 系统内：A施工、B劳务、C商贸、D设备租赁。
- 系统外：甲劳务、乙设备、丙材料、丁专业分包/其他。
- ABCD各自独立会计和纳税；项目综合分析抵消ABCD内部交易，再以B工资社保、C外采材料、D折旧维修燃料人员等真实底层成本替代。
- 甲乙丙丁只进入外部成本、进项、付款、履约和风险。

## 正式模块
主数据、项目、预算、合同、数电票、银行资金、真实成本、履约证据、VAT/CIT/印花/附加/预缴、EAC、方案沙盘、风险、审计、批量导入、RBAC、备份。

## 生产数据库
PostgreSQL；金额NUMERIC(18,2)；法人/项目/期间复合索引；月结锁；软删除；不可变审计日志。

## 安全
税额只能由版本化规则和确定性代码计算。税务规则保存有效期、法规来源、复核人和复核状态；未经复核不得用于正式申报。


## AI检查编排层

V2.3增加独立AI Review Orchestrator：

Web UI
→ AIReviewJob
→ 本地确定性检查
→ scope数据选择
→ sanitize_context脱敏
→ Model Adapter
→ AIReviewResult
→ 审计日志。

支持多个AIModelEndpoint。当前实现：
- mock
- openai_compatible

API Key只从环境变量读取。
