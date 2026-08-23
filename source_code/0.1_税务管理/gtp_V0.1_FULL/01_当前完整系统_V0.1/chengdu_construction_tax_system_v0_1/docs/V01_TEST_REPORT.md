# V0.1 测试报告

## 1. Web页面回归

已验证HTTP 200：

- `/`
- `/project/1`
- `/manage`
- `/matching`
- `/tax-ledger`
- `/risks`
- `/imports`
- `/audit`
- `/ai-review`
- `/ai-models`
- `/ai-prompts`
- `/health-check`
- `/tasks`
- `/docs`

## 2. 单环节检查

测试：设备专项检查。

结果：
- 创建AIReviewJob成功；
- 自动选择设备专项Prompt；
- 结果正常保存；
- 详情页可正常打开。

## 3. 双模型标准项目体检

配置：
- 宜宾示范工业项目
- standard档位
- 2个Mock模型

执行：
- 8个Scope
- 2个模型
- 共16次AI子检查

结果：
- 16/16完成；
- Batch状态 completed；
- 共识引擎生成Common Findings；
- 能生成模型Differences；
- 综合风险和综合评分生成正常。

典型测试输出：

- 8项共同/确定发现
- 1项模型差异

## 4. 整改闭环

测试：

体检建议
→ 创建RemediationTask
→ 标记done
→ 选择模型执行整改后复检
→ 创建新的AIReviewJob
→ 任务状态变为rechecked
→ 保存recheck_job_id。

全部通过。

## 5. Prompt版本测试

- Seed包含default及合同、税务、设备、劳务、材料、整项目等专项模板；
- 新增设备Prompt v2成功；
- 最新启用版本可以被自动选择；
- AIReviewJob记录prompt_template_id。

## 6. OpenAI-compatible真实HTTP链路测试

使用本机临时HTTP Server模拟兼容接口：

系统
→ HTTP POST `/v1/chat/completions`
→ 外部服务返回 `choices[0].message.content`
→ 严格JSON解析
→ AIReviewResult持久化。

验证：
- status = completed
- score = 87
- Prompt专项重点已进入发送messages
- prompt_template_id正确记录

测试通过。

## 7. 当前限制

- 健康体检目前同步执行；真实多个外部模型可能耗时，生产版应使用队列；
- Mock模型用于功能演示，不代表专业结论；
- 多模型共识目前使用字段归一化匹配，后续可增加语义聚类；
- 整改任务目前按责任“角色”，尚未接用户/组织架构；
- 尚未生成正式PDF/DOCX体检报告。
