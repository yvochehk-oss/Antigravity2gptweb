# V2.3 AI检查编排测试报告

## 1. 离线Mock端到端测试

通过范围：
- contract 合同
- equipment 设备
- tax 税务
- whole_project 整个项目

检查流程：
项目数据 → 构造检查包 → 脱敏 → Mock模型 → 结构化结果 → AIReviewResult。

所有Job均完成并写入：
- risk_level
- score
- summary
- findings
- recommendations
- data_gaps
- input_digest
- raw_response

## 2. Web测试

HTTP 200：
- `/`
- `/ai-review`
- `/ai-models`
- `/ai-review/1`
- `/api/ai-review/1`

POST：
- `/ai-review/run` 返回303并创建新检查Job。

## 3. 典型结果

宜宾示范项目整项目检查：
- 能读取确定性四流异常
- 能识别乙设备履约证据不完整
- 能提示尚未专业复核的税务规则
- 不修改本地税务和项目利润数字

## 4. 安全测试点

- API Key不存数据库，只保存环境变量名
- Job保存输入摘要哈希
- 用户可查看发送数据预览
- 模型原始响应保留便于追溯
- 外部模型失败不影响本地确定性业务计算

## 5. 当前限制

- V2.3同步调用模型；生产版建议任务队列
- 当前正式外部适配器为OpenAI-compatible
- 尚未实现Token/费用统计
- 尚未实现多模型交叉复核
- 脱敏器目前是框架，待真实字段进入后增加身份证、银行账号等规则


## 6. OpenAI-compatible HTTP适配器真实链路测试

使用本机临时HTTP服务器模拟 `/v1/chat/completions`：

- 系统真实发出HTTP POST
- 请求中包含 model 和 messages
- 兼容API返回 `choices[0].message.content`
- content为严格JSON审查结果
- 系统成功解析并写入AIReviewResult

测试结果：
- status = completed
- risk_level = MEDIUM
- score = 88
- summary = 外部兼容API测试成功

说明：该测试不访问互联网，但验证了真实HTTP调用、响应解析、Job状态和结果持久化整条适配器链路。
