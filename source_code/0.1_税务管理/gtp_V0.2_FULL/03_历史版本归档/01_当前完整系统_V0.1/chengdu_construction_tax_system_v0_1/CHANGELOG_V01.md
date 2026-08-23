# V0.1 Changelog

- 新增项目一键综合体检：quick / standard / deep。
- 新增多模型交叉复核与本地共识汇总。
- 新增模型差异意见展示。
- 新增AIPromptTemplate及Prompt版本库页面。
- 新增RemediationTask整改任务中心。
- 新增从单次AI建议/综合体检建议生成整改任务。
- 新增整改完成后的AI复检和父子检查追溯。
- 新增项目页“一键综合体检”入口。
- 新增 `/health-check`、`/tasks`、`/ai-prompts` 和 `/api/health-check/{id}`。
- AIReviewJob记录实际Prompt模板ID和复检parent_job_id。
- Mock双模型用于离线演示共识/差异功能。
- 完成OpenAI-compatible真实HTTP适配器+Prompt模板测试。
