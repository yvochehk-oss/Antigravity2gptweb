# 成都建筑施工企业综合经营与税务统筹系统 V0.1

## 主体编号

### 系统内
- A：施工企业
- B：劳务公司
- C：商贸公司
- D：设备租赁公司

### 系统外
- 甲：第三方劳务公司
- 乙：第三方设备租赁公司
- 丙：外部材料供应商
- 丁：外部专业分包/其他供应商

## V0.1主要能力

### 项目经营与业财税
- 项目真实成本穿透
- ABCD内部交易与项目综合口径分离
- 合同、履约、发票、付款四流匹配
- 成本科目树
- ABCD法人独立税务管理台账
- EAC预计完工
- 风险扫描
- CSV导入
- 操作审计

### AI检查
- 单环节随时检查
- 用户自定义检查要求
- 外部OpenAI-compatible模型API
- API Key环境变量管理
- 数据最小化和脱敏入口
- Prompt模板版本管理

### V0.1新增
- 快速/标准/深度一键项目体检
- 一个项目同时选择多个模型
- 多模型共同发现与差异意见
- 确定性共识汇总
- AI建议生成整改任务
- 整改任务状态管理
- 整改后AI复检
- 检查 → 整改 → 复检完整追溯链

## Mac启动

双击：

`run_demo_mac.command`

或：

```bash
./run_demo.sh
```

浏览器：

`http://127.0.0.1:8765`

## 核心页面

- `/` 经营驾驶舱
- `/project/1` 项目经营页
- `/matching` 四流匹配
- `/tax-ledger` ABCD法人税务管理台账
- `/risks` 风险中心
- `/ai-review` 单环节AI检查
- `/health-check` 项目一键综合体检
- `/tasks` 整改任务中心
- `/ai-prompts` Prompt模板版本库
- `/ai-models` 外部模型API配置
- `/audit` 审计日志
- `/docs` FastAPI文档

## 外部模型API

数据库不保存真实API Key，只保存环境变量名。

例如模型端点设置：

`api_key_env = TAX_AI_API_KEY`

Mac启动前：

```bash
export TAX_AI_API_KEY="你的密钥"
./run_demo.sh
```

OpenAI-compatible模型可以配置：
- Base URL
- Chat Path
- Model
- API Key环境变量
- Timeout

本地模型或公司内部AI网关如果兼容该协议也可接入。

## 重要边界

1. A/B/C/D法律、会计和税务独立。
2. 项目综合经营口径抵消ABCD内部交易并穿透真实底层成本。
3. 甲乙丙丁仅作为外部交易对手和外部成本变量。
4. 税额、项目利润、四流和EAC由确定性程序计算。
5. AI只负责审查、解释、发现遗漏和提出建议，不得覆盖确定性结果。
6. 当前税务规则仍有未专业复核标记，Demo不可直接作为正式纳税申报依据。
7. Demo默认SQLite；生产版建议PostgreSQL。

## 文档

- `docs/V01_DESIGN.md`
- `docs/V01_TEST_REPORT.md`
- `docs/AI_REVIEW_ARCHITECTURE.md`
- `docs/V22_DESIGN.md`
- `docs/ARCHITECTURE.md`
