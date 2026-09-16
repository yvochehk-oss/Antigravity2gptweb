---
name: safari_chatgpt_reasoner
description: 双层混合 Agent 系统：以 Safari/Chrome ChatGPT 网页端 Custom GPT 为云端认知与代码修改控制面（配备 GitHub 直连读写工具，直接在 GitHub 仓库执行修改与推送），以任意本地桌面 Agent 为确定性执行与验收数据面（收到推送后 git pull 拉取代码并运行本地测试进行最终闭环验收）。强制执行 Tab 精确绑定 + 跨进程事务锁 + 闭环测试验收，证据完整性真正可机验。
---

# Safari / Chrome ChatGPT Reasoner Skill (v4.1 Evidence-Integrity)

### 架构原则与契约 (Architecture Contracts)
1. **【第一铁律】Custom GPT GitHub 直连修改与本地 Agent 闭环拉取验收 (P0 - Supreme Law)**：
   - **Custom GPT 云端直连 GitHub 读写与推送**：Safari / Chrome 上的 Custom GPT 配备了 GitHub 直连读写工具，直接指示 ChatGPT 在 GitHub 远程仓库上执行文件修改、分支管理与提交推送（commits / PRs 由 Custom GPT 直接在 GitHub 仓库落地）。
   - **本地 Agent 职责严格为 `git pull` 与本地测试闭环验收**：本地 Agent 严禁越俎代庖自主编写业务代码；其核心职责是在收到 ChatGPT 的远程修改与推送后，执行 `git pull` 同步拉取最新代码，在本地真实环境中运行测试命令（如 pytest / npm test），收集标准输出、错误日志与退出码，并忠实反馈给 Custom GPT 进行审查（`task-review`），由 Custom GPT 裁决 `APPROVED` 或发起新一轮修复推送，完成全流程闭环验收。
2. **三权分立与单向闭环**：
   - **Reasoning & Modification Plane（Custom GPT Web）**：高阶架构推演、故障归因、直接通过 GitHub 工具操作远程仓库代码。
   - **Execution & Validation Plane（本地 Agent）**：`git pull` 拉取代码、依赖安装、运行本地测试验证（高权限确定性操作）。
   - **Verification Plane（Evidence Store）**：独立以 exit code、测试日志、事件 JSONL、git commit SHA 为客观事实准绳。
3. **URL + Transaction + User + Assistant 四重身份绑定 (P0)**：
   - 调用方必须通过 `--target-url` 显式指定 ChatGPT 会话 URL（仅接受 `https://chatgpt.com` / `https://www.chatgpt.com`）。
   - 同一 `target_url` 在任意时刻只允许一个 RPC（`TargetTabLock`，`fcntl.flock(LOCK_EX|LOCK_NB)`）。
   - 提交验证不再用"消息数量 +2"启发式；改在 JS 端规范化比对 last user message 是否 === expected prompt。
   - 助手稳定阶段只读 `data-message-id` 唯一定位的目标 turn；若节点消失/身份变化即视为证据失效（不再依赖 totalCount）。
4. **渐进式证据等级 (L0 – L3)**：
   - L0：**不传 Evidence 正文**，仅传调用上下文与 git 状态摘要。
   - L1：折叠中段，保留头 5 行 + 尾 35 行。
   - L2：保留尾部 80 行。
   - L3：全文（建议改用 `--evidence-file`）。
5. **结构化退出码 (Verification Plane 唯一判据)**：
   - `0`: 正常完成，证据完整
   - `2`: 超时（拿到部分内容，`TIMEOUT_PARTIAL`）
   - `3`: 超时（无内容，`TIMEOUT_EMPTY`）
   - `4`: 浏览器 / JS 执行异常 (Safari: `SAFARI_FAIL`, Chrome: `BROWSER_FAIL`)
   - `5`: 基线采集失败 (`BASELINE_FAIL`)
   - `6`: 用户消息未真正提交 (`SUBMIT_FAIL`)
   - `7`: 助手新回合未产生 (`NO_NEW_TURN`)
   - `10`: 目标 Tab 不存在 (`NO_TAB`)
   - `11`: 目标 Tab 歧义 (`AMBIGUOUS_TAB`)
   - `12`: 熔断器已开 (`CIRCUIT_OPEN`)
   - `13`: 目标 Tab 正在被另一 bridge 占用 (`TARGET_BUSY`)
   - 所有阶段事件走 stderr JSON（递归脱敏凭据），stdout 保持纯文本回答。
6. **时效与 deadline (P0-4)**：
   - `--timeout` 是 monotonic 绝对 deadline，覆盖 baseline → 注入 → 提交 → 新回合 → 稳定 全过程。
   - 任何阶段用 `min(phase_budget, remaining_deadline)` 切片；超时即返回当前最新事实。
7. **写操作 fail-fast (P1-4)**：
   - 本版本**不再**自动重试 Safari 写操作（`inject` / `send` 非幂等）。
   - 失败立即抛错并退出，宁可让调用方显式重试，也不要猜测。
8. **熔断器 (P1-1 / P1-2 / P1-3)**：
   - 滑动时间戳窗口（默认 1h 内 ≤3 次同签名失败即熔断）。
   - 每次 read-modify-write 自动 prune 过期签名；空 entry 直接删除，state JSON 不会无限增长。
   - `reset_circuit_breaker` 自身持锁，并可通过 `--reset-circuit` 显式调用。
9. **双层职责绝对隔离与防抢跑纪律 (Strict Division of Labor & Anti-Preemption Constraint)**：
   - **GPT 独占方案与代码修改推送权**：所有架构方案、实施任务分解、代码修改提交与测试命令指定，由配备 GitHub 工具的 Custom GPT 深度推演并在远端仓库执行。
   - **本地 Agent 严禁自主编写业务代码**：本地 Agent 绝对禁止越俎代庖自己编写业务代码或擅自更改方案。
   - **绝对防抢跑纪律**：当 Custom GPT 处于深度思考（Reasoning）、工具调用（调用 GitHub Action 提交代码）或流式生成时，本地 Agent 必须耐心等待完整推送与响应完成，绝对不得以“耗时较长”为由抢跑编写代码。
   - **最终闭环判据**：每一个任务的闭环必须由 GPT 在 `task-review` 阶段审查本地实际测试日志并显式输出 `APPROVED` 裁决，方可进入下一任务或交付。
10. **【强制元数据基线声明契约 (Mandatory Metadata Baseline Contract)】**：
    - **Prompt 顶部强制注入三要素基线**：在与 Safari / Chrome ChatGPT 交互的每一个 Prompt 最顶部，必须显式注入结构化元数据块（目标 GitHub 仓库全称、目标实施分支、最新的 Commit Hash / Parent Commit、核心子模块的实际物理相对路径）。
    - **消除检索漂移**：严禁省略或让 ChatGPT 猜测默认分支与路径，彻底杜绝多子系统（如 `source_code/0.1_税务管理/...`）及非默认私有分支（如 `v3.0-macos`）的检索盲区与路径错配。
    - **标准注入范例**：
      ```markdown
      【@GitHub 协同基线强制对齐】
      1. 目标仓库：<owner>/<repo>
      2. 目标分支：<branch_name>
      3. 最新提交基线（Parent Commit）：<commit_sha>
      4. 核心子系统物理路径：
         - 模块 A：source_code/.../moduleA
         - 模块 B：source_code/.../moduleB
      ```
11. **【执行计划单步流式推进与闭环核准契约 (Step-by-Step Plan Execution & Approval Contract) - P0】**：
    - **严禁打包批量推送**：当任务已有详细执行计划（如包含多个原子 Commit / 步骤）时，本地 Agent **绝对严禁**一次性将整个多步计划打包发送给 GPT，必须严格按照计划**一条一条地**单步推进。
    - **GPT 提交后必附检验命令**：GPT 使用 GitHub 直连工具在远端完成该单步的修改与推送后，必须按规范显式给出本地检验命令（`TEST: <命令>` / `EXPECTED: <预期断言与退出码>`）。
    - **本地 Agent 严格执行检验并回传日志**：本地 Agent 执行 `git pull` 同步最新代码后，**必须先完整跑完 GPT 给出的检验命令**，收集标准输出、错误日志与退出码，并通过 bridge 将检验结果回传给 GPT 审查。
    - **GPT 裁决同意后方可推进下一条**：只有当 GPT 审查本地真实测试证据并显式输出 `APPROVED` 确认后，本地 Agent 方可将计划中的**下一条**任务发送给 GPT；未获同意前必须在当前任务内闭环返工，严禁跨步骤抢跑。
12. **【轮询保活与故障自动重连自愈契约 (Auto-Recovery & Keep-Alive Polling Contract) - P0】**：
    - **轮询守护进程防中断**：本地 Agent 必须通过常驻 Node.js 守护进程（如 `orchestrate.js` 或外层任务调度器）对 Chrome / Edge 中的 ChatGPT 状态进行不间断监视。
    - **异常自动重新拉起 (Auto-Restart / Relaunch)**：若底层 AppleScript/CDP 探针因页面卡顿、超时（如 `SUBMIT_FAIL`）或浏览器窗口丢失等异常中断，轮询进程**必须具备捕获异常并自动重新拉起（Auto-Relaunch）的自愈机制**，严禁让盯盘静默挂起。
    - **快照续接与防重发**：重新拉起后，须比对上一次获取的 `lastUserMessageId` 或 DOM 文本快照平滑续接，不得重复提交已发送的 Prompt。
13. **【自动 Git 代码拉取与 HEAD 比对校验契约 (Auto Git Pull & Evidence Sync Contract) - P0】**：
    - **自动触发 `git pull --ff-only`**：在 Custom GPT 于 GitHub 远端完成修改并给出 `TEST:` 指令后，本地 Agent（或 `orchestrate.js` 脚本）在跑测前必须 fast-forward 拉取目标分支；工作区不干净时必须停止。
    - **HEAD 比对与状态防落后**：拉取后通过 `git rev-parse HEAD` 与 `git rev-parse origin/<branch>` 进行基线比对，确保远端提交已被完整同步至本地工作区后再执行测试。
    - **保持工作树 Clean**：在执行 `git pull` 前确保工作区处于 clean 状态，防止生成非必要的 merge commit。
14. **【高管经营内参通用文案与零代码黑话铁律 (Executive Briefing & Zero Jargon Contract) - P0】**：
    - **高管通用视角**：全系统与 AI 交互展示模板统一采用**“高管经营内参”**（如 `**【高管经营内参 · 集团概览】**`），禁止硬编码专属职务字样（如“董事长”）。
    - **零代码黑话泄漏**：严禁在面向高管与业务人员的界面及 Copilot 回复中泄漏任何代码层英文黑话（如 `Canonical Facts`、`project_id`、`cockpit/summary`、`analytics_*` 等）。

### 标准调用模式

> **标准跨平台 Bridge 通道**：基于 Node.js 原生 API 实现（`scripts/chrome_chatgpt.js`），支持 Windows / macOS / Linux 全平台，零 npm 依赖，零 Python 依赖。已彻底剔除旧版 Unix 独占的 Python `fcntl` 实现。

#### Node.js 跨平台标准 Bridge (`chrome_chatgpt.js`)

前置条件：启动目标浏览器（Chrome 或 Edge）并开启 CDP 调试端口后，导航至 ChatGPT 会话页：

```cmd
:: Windows 下启动 Chrome / Edge
chrome.exe --remote-debugging-port=9222 --remote-allow-origins=*
:: 或
msedge.exe --remote-debugging-port=9222 --remote-allow-origins=*
```

Node.js Bridge 标准调用方法（全平台统一使用 `node chrome_chatgpt.js`）：

```bash
# 1. 精确指定 Tab 执行架构规划与 Prompt 发送
node .agents/skills/safari-chatgpt-reasoner/scripts/chrome_chatgpt.js \
  --target-url "https://chatgpt.com/c/6a9f7b81-0bcc-83e9-a4c1-036d110e1665" \
  --type plan \
  --prompt "任务目标描述"

# 2. 本地执行测试日志反馈闭环（带 L1 渐进脱敏与熔断保护）
node .agents/skills/safari-chatgpt-reasoner/scripts/chrome_chatgpt.js \
  --target-url "https://chatgpt.com/c/6a9f7b81-0bcc-83e9-a4c1-036d110e1665" \
  --type feedback \
  --prompt "正在执行模块 A 重构" \
  --evidence-file /tmp/pytest_fail.log \
  --level L1 \
  --signature "ALEMBIC_MIGRATION_DUPLICATE_KEY_ERR"

# 3. 指定 Edge 或非默认端口 / host
node .agents/skills/safari-chatgpt-reasoner/scripts/chrome_chatgpt.js \
  --browser-name edge \
  --chrome-host 127.0.0.1 \
  --chrome-port 9222 \
  --target-url "https://chatgpt.com/c/6a9f7b81-0bcc-83e9-a4c1-036d110e1665" \
  --type plan \
  --prompt "用 Edge 调用"

# 4. 仅清空熔断器
node .agents/skills/safari-chatgpt-reasoner/scripts/chrome_chatgpt.js \
  --reset-circuit
```

> **注意**：
> - 本 Windows 分支仅包含 Node.js 运行时；`chrome_chatgpt.js` 使用标准库，不需要 npm 包或 Python。
> - **不要同时在 9222 端口启两个 Chromium 实例**：CDP 端口冲突会让 `/json/list` 返回错乱 Tab。建议 Edge 用户把端口改成 9223，并在调用时 `--chrome-port 9223`。

### 下游消费规范

**重定向 answer 到文件**（Windows Node.js）：

```bash
node scripts/chrome_chatgpt.js --target-url "..." --prompt "..." >answer.txt 2>events.jsonl

ec=$?
case $ec in
  0)   cat answer.txt ;;          # OK
  2)   cat answer.txt ;;          # TIMEOUT_PARTIAL
  3|4|5|6|7|10|11|12|13)
       echo "bridge failed ec=$ec, see events.jsonl" >&2
       exit "$ec" ;;
esac
```

事件流（stderr JSONL）示例：
```jsonl
{"ts": 1725000001.0, "stage": "baseline", "exit_code": 0, "exit_name": "OK", "message": "基线采集成功", "browser": "safari", "totalCount": 12, "userCount": 6, "assistantCount": 6, "lastUserMessageId": "msg_abc"}
{"ts": 1725000003.5, "stage": "submit_verify", "exit_code": 0, "exit_name": "OK", "message": "用户消息已提交（exact prompt match）", "browser": "safari", "newUserCount": 7, "newUserMessageId": "msg_def", "newTextLen": 842}
{"ts": 1725000045.8, "stage": "new_turn", "exit_code": 0, "exit_name": "OK", "message": "助手新回合已开始", "browser": "safari", "newAssistantCount": 7, "messageId": "msg_ghi", "firstTextLen": 31}
{"ts": 1725000060.2, "stage": "done", "exit_code": 0, "exit_name": "OK", "message": "捕获到最终生成内容（字数=1842）", "browser": "safari", "charCount": 1842, "targetMessageId": "msg_ghi"}
```

### 新会话流程

v4.1 **删除** `--new` 参数。新会话的开启由 Execution Plane 在调用前完成（Safari 和 Chrome 通用）：

1. Execution Plane 打开目标 ChatGPT 会话页（或调用任何外部手段拿到最终 `/c/<uuid>`）。
2. 把最终 URL 通过 `--target-url` 传入本桥。
3. 由本桥以 Hard Binding 接管该会话的全部交互。

这样 Hard Tab Binding 与"主动切换会话 URL" 不再存在结构性冲突。

---

## Orchestrator：端到端任务编排器

`scripts/orchestrate.js` 在 bridge 之上构建了 Windows 的完整任务闭环；本分支不依赖 Python。

### 架构

```
开发者需求
    │
    ▼
orchestrate.js init ────→ GPT 生成方案（plan）
    │
    ▼
orchestrate.js lock ────→ 解析为任务列表
    │
    ▼
orchestrate.js run-task × N ────→ 每个任务的闭环：
    │
    ├── GPT 生成代码（task-code）
    ├── 本地 Agent 写文件
    ├── git add + commit + push
    ├── 本地 Agent 跑测试
    ├── GPT 审查测试结果（task-review）
    └── 裁决：APPROVED / NEEDS_FIX（自动修复） / BLOCKED（暂停等人工）
    │
    ▼
全部任务完成 → 项目交付
```

### 新增 `--type` 协议与输出规范

| type | 用途 | GPT 输出要求 |
|---|---|---|
| `task-code` | 单任务代码生成与提交 | 代码由 Custom GPT 直接通过 GitHub 直连工具在远端完成提交与推送。在对话回复中：**5. 只测试命令，不要输出代码，不要写说明文字。** 测试命令以 `TEST:` 标记，预期结果以 `EXPECTED:` 标记。 |
| `task-review` | 代码 + 测试结果审查 | 只输出 `APPROVED` / `NEEDS_FIX(原因)` / `BLOCKED(原因)` |

【输出格式要求】（严格遵守）：
1. 代码直接使用 GitHub 直连工具提交至目标远程分支。
2. 禁止在对话回复中粘贴大段代码，避免冗余和格式截断。
3. 测试命令用以下格式（放在单独的 bash 块中）：
   `TEST: <实际命令>`
   `EXPECTED: <预期结果描述>`
4. 如果任务涉及多文件，请按依赖顺序排列。
5. 只测试命令，不要输出代码，不要写说明文字。

### Orchestrator 子命令速查

```bash
# 初始化项目（生成初始方案）
node scripts/orchestrate.js init \
  --name my-migration \
  --requirement "把 Flask 认证迁移到 FastAPI + JWT" \
  --target-url "https://chatgpt.com/c/xxx" \
  --repo git@github.com:xxx/yyy.git \
  --cwd C:\\path\\to\\repo \
  --branch feature/my-migration

# 锁定方案（解析为任务列表，提交到 GitHub）
node scripts/orchestrate.js lock --name my-migration

# 执行单个任务（交互模式：每步等用户）
node scripts/orchestrate.js run-task --name my-migration --task-id 1

# 查看状态
node scripts/orchestrate.js status --name my-migration
```

### 状态文件

- `~/.antigravity/orchestrator/<name>.state.json` — 完整项目状态
- `~/.antigravity/orchestrator/<name>/PLAN.md` — 当前方案（可编辑）
- `~/.antigravity/orchestrator/<name>/TASKS.json` — 解析后的任务列表

每个任务状态：
- `pending` → `coding` → `testing` → `approved` / `failed` / `blocked`

### 单任务闭环流程（GitHub 直连工具与本地验收）

```
┌─────────────────────────────────────────────────────────┐
│ 1. Safari Custom GPT (配 GitHub 工具) 远端修改与推送    │
│    - 直接调用 GitHub 工具对远程仓库执行修改与 commit/push │
│    - 给出本地测试命令（如 pytest / npm test）             │
│    - 说明预期测试结果（如 "all tests pass", "exit 0"）    │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 2. 本地 Agent 执行 git pull 拉取代码                     │
│    - 检测或收到推送后执行 git pull 获得最新代码          │
│    - 确认分支与 commit SHA 处于一致状态                  │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 3. 本地 Agent 执行本地测试                               │
│    - 运行 Custom GPT 提供的测试验证命令                  │
│    - 捕获 stdout / stderr / exit code                   │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 4. 本地 Agent 反馈测试结果给 Custom GPT                  │
│    - 测试命令 + 实际运行日志与错误堆栈                   │
│    - 通过 / 失败状态（exit code）                        │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 5. Custom GPT 审查并决定下一步                           │
│    - APPROVED：测试通过，任务完成 ✅                     │
│    - NEEDS_FIX：测试失败，Custom GPT 再次远程推送修复 🔧 │
│    - BLOCKED：环境/依赖受阻，需人工介入 🚫               │
└─────────────────────────────────────────────────────────┘

【自动修复循环】
NEEDS_FIX → Custom GPT 重新修改并 push → Agent git pull + 测试 → GPT 审查
最多 3 轮（可配置），避免无限循环
```

每个任务状态：
- `pending` → `pulling` → `testing` → `approved` / `failed` / `blocked`
- 修复中：`fixing` → `pulling` → `testing` → 循环或完成

### 关键设计原则

- **第一铁律（GitHub 直连修改 + 本地 pull 验收）**：Safari 上的 Custom GPT 配备 GitHub 直连读写工具直接在远程仓库修改并推送；本地 Agent 执行 `git pull` 同步最新代码并在真实环境运行测试，完成闭环验收。
- **测试驱动闭环**：Custom GPT 必须给出测试验证命令，Agent 跑测试后把真实结果反馈给 GPT，由 GPT 根据实际测试日志决定通过/修复/阻塞，避免主观判断。
- **强制测试命令**：Custom GPT 交付改动时必须提供至少一个测试命令，确保每次提交都可被本地物理验证。
- **自动修复机制**：测试失败时，Custom GPT 会根据真实报错信息直接远程修复并重新推送，最多尝试 3 轮。每轮都是完整的“远程修改推送 ➔ 本地 git pull ➔ 本地测试 ➔ 审查裁决”闭环。
- **职责边界清晰**：Custom GPT 负责高阶认知、方案与远程代码改动，本地 Agent 负责拉取、本地执行与环境验证。推演出错找 GPT，落地与环境出错找 Agent。
- **可恢复**：所有状态持久化到 `~/.antigravity/orchestrator/`，中断后可随时恢复。
- **长会话平滑交接 (Handoff Protocol)**：当对话历史过长导致 WebKit/页面渲染负载增加时，执行 `旧会话生成交接摘要 ➔ 换新会话 URL 注入继续`，兼顾 100% 上下文继承与极致流畅度。
- **可信证据**：所有 bridge 调用走事件 JSONL，stderr 记录每次 GPT 请求/响应的基线快照。

