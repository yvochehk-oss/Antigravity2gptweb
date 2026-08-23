# V0.1 项目一键体检、多模型交叉复核与整改闭环设计

## 1. V0.1目标

V0.1把V2.3“单次AI检查”升级为可持续的项目审查体系：

1. 用户可随时检查一个环节；
2. 用户可一键启动整个项目体检；
3. 同一环节可同时使用多个模型；
4. 系统区分模型共同确认的问题和模型差异；
5. AI建议可转为整改任务；
6. 整改完成后可直接复检；
7. Prompt按检查范围做版本管理；
8. 全部检查、整改和复检保留追溯链。

## 2. 一键体检档位

### quick 快速
- 项目总体
- 风险
- EAC

### standard 标准
- 合同
- 履约
- 发票
- 资金/付款
- 真实成本
- 税务
- EAC
- 风险

### deep 深度
标准体检全部内容，再增加：
- 材料
- 劳务
- 设备
- 专业分包

## 3. 多模型交叉复核

用户可在一次体检中勾选一个或多个AIModelEndpoint。

运行方式：

项目 × 检查范围 × 模型端点 → AIReviewJob

例如标准体检选择2个模型：

8个范围 × 2个模型 = 16个独立子检查。

每个子检查都保存：
- Scope
- 模型
- Prompt版本
- 用户特别要求
- 输入摘要哈希
- 输入预览
- Findings
- Recommendations
- Data Gaps
- 原始响应

## 4. 共识引擎

AIConsensusReport不依赖模型再次“总结模型”，而由本地程序确定性汇总：

- 多模型对同一Scope提出相同/高度一致的问题 → Common Findings；
- 只有单个模型提出的问题 → Differences；
- 综合评分 → 子检查评分平均；
- 综合风险 → 取最高风险等级；
- 建议 → 去重合并；
- Data Gaps → 去重合并。

这样可以避免最终总结模型覆盖或扭曲原始检查结论。

## 5. Prompt模板版本管理

新增 AIPromptTemplate：

- name
- scope
- version
- system_addendum
- review_focus
- enabled
- created_at

运行时：

1. 优先使用该Scope最新启用版本；
2. 没有专项模板时回退到default；
3. AIReviewJob记录实际使用的prompt_template_id；
4. 检查结果页显示模板名称和版本。

因此合同、税务、材料、劳务、设备等可以分别维护专业Prompt。

## 6. 整改任务闭环

新增 RemediationTask。

来源可以是：
- 单次AIReviewJob；
- 整项目AIReviewBatch。

任务保存：
- 项目
- Scope
- 来源检查
- 标题
- 整改说明
- 优先级
- 责任角色
- 状态
- 创建/更新时间
- 复检Job

状态：
- open
- in_progress
- done
- rechecked
- verified
- closed

AI不能自动关闭整改任务。

## 7. 整改后复检

用户完成整改、更新系统数据后，在任务中心点击“整改后复检”：

RemediationTask
→ 创建新的AIReviewJob
→ 使用该任务Scope
→ 自动附加“这是整改后复检”的指令
→ 保留parent_job_id
→ 新结果写入任务recheck_job_id

人工查看复检结果后，再决定 verified / closed。

## 8. 安全边界不变

V0.1继续强制：

- 本地确定性引擎负责利润、税务管理测算、四流、EAC和规则风险；
- 外部模型负责审查、解释、找遗漏和建议；
- 外部模型不得修改系统确定性数字；
- API Key只使用环境变量；
- 按Scope最小化发送数据；
- 保留sanitize_context脱敏入口；
- 所有模型调用都有审计记录。

## 9. 主要Web页面

- `/ai-review`：单环节AI检查
- `/health-check`：一键项目综合体检
- `/health-check/{id}`：综合体检及多模型共识结果
- `/tasks`：整改任务中心
- `/ai-prompts`：Prompt模板版本库
- `/ai-models`：外部模型API端点
- `/audit`：操作审计

## 10. 生产版下一步

V0.2建议：

1. AI调用改为异步任务队列；
2. SSE/轮询显示检查进度；
3. Token和调用费用统计；
4. 模型端点按部门/角色授权；
5. 外发数据白名单策略；
6. Prompt审批和发布流程；
7. 整改任务增加责任人、截止日期、附件；
8. 体检报告导出PDF/DOCX；
9. 自动定期项目健康检查；
10. PostgreSQL生产迁移与RBAC。
