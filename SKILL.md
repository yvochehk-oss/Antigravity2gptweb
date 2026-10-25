---
name: safari-chatgpt-reasoner
description: 双层混合 Agent 系统：以 Safari/Chrome ChatGPT 网页端 Custom GPT 为云端认知与代码修改控制面（配备隧道插件，直接读写本机工作目录中的文件），以任意本地桌面 Agent 为确定性执行与验收数据面（核对文件变更清单、运行本地测试进行最终闭环验收；任务前自动快照，可一键回滚）。强制执行 Tab 精确绑定 + 跨进程事务锁 + 闭环测试验收，证据完整性真正可机验。
---

# Safari / Chrome ChatGPT Reasoner Skill (v4.1 Evidence-Integrity)

### 架构原则与契约 (Architecture Contracts)
1. **【第一铁律】Custom GPT 隧道直读写本机文件与本地 Agent 闭环验收 (P0 - Supreme Law)**：
   - **Custom GPT 经隧道插件直接读写本机工作目录**：Safari / Chrome 上的 Custom GPT 配备了隧道插件，直接指示 ChatGPT 读写本机工作目录（`--cwd` 指定）中的文件完成修改，无 GitHub 中转、无提交/推送环节。
   - **本地 Agent 职责严格为变更核对与本地测试闭环验收**：本地 Agent 严禁越俎代庖自主编写业务代码；其核心职责是在每个任务开始前给工作目录做快照并记录文件清单（sha256），GPT 声称完成后以清单差异核对实际落盘变更（零变更直接判 BLOCKED），在本地真实环境中运行测试命令（如 pytest / npm test），收集标准输出、错误日志与退出码，并忠实反馈给 Custom GPT 进行审查（`task-review`），由 Custom GPT 裁决 `APPROVED` 或发起新一轮原地修复，完成全流程闭环验收。出问题可用 `orchestrate.py restore` 从快照回滚。
2. **三权分立与单向闭环**：
   - **Reasoning & Modification Plane（Custom GPT Web）**：高阶架构推演、故障归因、直接通过隧道插件读写本机工作目录文件。
   - **Execution & Validation Plane（本地 Agent）**：任务前快照与文件清单基线、变更差异核对、依赖安装、运行本地测试验证（高权限确定性操作）。
   - **Verification Plane（Evidence Store）**：独立以 exit code、测试日志、事件 JSONL、文件清单（sha256）差异为客观事实准绳。
3. **URL + Transaction + User + Assistant 四重身份绑定 (P0)**：
   - 调用方必须通过 `--target-url` 显式指定 ChatGPT 会话 URL（仅接受 `https://chatgpt.com` / `https://www.chatgpt.com`）。
   - 同一 `target_url` 在任意时刻只允许一个 RPC（`TargetTabLock`，`fcntl.flock(LOCK_EX|LOCK_NB)`）。
   - 提交验证不再用"消息数量 +2"启发式；改在 JS 端规范化比对 last user message 是否 === expected prompt。
   - 助手稳定阶段只读 `data-message-id` 唯一定位的目标 turn；若节点消失/身份变化即视为证据失效（不再依赖 totalCount）。
4. **渐进式证据等级 (L0 – L3)**：
   - L0：**不传 Evidence 正文**，仅传调用上下文与工作目录文件清单摘要。
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
   - **GPT 独占方案与代码修改权**：所有架构方案、实施任务分解、代码修改与测试命令指定，由配备隧道插件的 Custom GPT 深度推演并直接在本机工作目录执行。
   - **本地 Agent 严禁自主编写业务代码**：本地 Agent 绝对禁止越俎代庖自己编写业务代码或擅自更改方案。
   - **绝对防抢跑纪律**：当 Custom GPT 处于深度思考（Reasoning）、工具调用（经隧道插件读写本机文件）或流式生成时，本地 Agent 必须耐心等待其完整响应完成，绝对不得以“耗时较长”为由抢跑编写代码。
   - **最终闭环判据**：每一个任务的闭环必须由 GPT 在 `task-review` 阶段审查本地实际测试日志并显式输出 `APPROVED` 裁决，方可进入下一任务或交付。
10. **【强制元数据基线声明契约 (Mandatory Metadata Baseline Contract)】**：
    - **Prompt 顶部强制注入三要素基线**：在与 Safari / Chrome ChatGPT 交互的每一个 Prompt 最顶部，必须显式注入结构化元数据块（本机工作目录绝对路径、本轮开始前的文件清单基线摘要、核心子模块的实际物理相对路径）。
    - **消除检索漂移**：严禁省略或让 ChatGPT 猜测目录与路径，彻底杜绝多子系统（如 `source_code/0.1_税务管理/...`）的检索盲区与路径错配。
    - **标准注入范例**：
      ```markdown
      【本机工作目录基线强制对齐】
      1. 工作目录：</abs/path/to/project>
      2. 本轮基线：任务开始前快照 + 文件清单（sha256）已由本地 Agent 记录
      3. 核心子系统物理路径：
         - 模块 A：source_code/.../moduleA
         - 模块 B：source_code/.../moduleB
      ```
11. **【执行计划单步流式推进与闭环核准契约 (Step-by-Step Plan Execution & Approval Contract) - P0】**：
    - **严禁打包批量执行**：当任务已有详细执行计划（如包含多个原子步骤）时，本地 Agent **绝对严禁**一次性将整个多步计划打包发送给 GPT，必须严格按照计划**一条一条地**单步推进。
    - **GPT 落盘后必附检验命令**：GPT 经隧道插件在本机工作目录完成该单步的修改后，必须按规范显式给出本地检验命令（`TEST: <命令>` / `EXPECTED: <预期断言与退出码>`）。
    - **本地 Agent 严格执行检验并回传日志**：本地 Agent 核对文件变更清单确认落盘后，**必须先完整跑完 GPT 给出的检验命令**，收集标准输出、错误日志与退出码，并通过 bridge 将检验结果回传给 GPT 审查。
    - **GPT 裁决同意后方可推进下一条**：只有当 GPT 审查本地真实测试证据并显式输出 `APPROVED` 确认后，本地 Agent 方可将计划中的**下一条**任务发送给 GPT；未获同意前必须在当前任务内闭环返工，严禁跨步骤抢跑。
12. **【轮询保活与故障自动重连自愈契约 (Auto-Recovery & Keep-Alive Polling Contract) - P0】**：
    - **轮询守护进程防中断**：本地 Agent 必须通过常驻 Python 守护进程（如 `poll_safari_chatgpt.py` 或 `orchestrate.py` 轮询循环）对 Safari / Chrome 中的 ChatGPT 状态进行不间断监视。
    - **异常自动重新拉起 (Auto-Restart / Relaunch)**：若底层 AppleScript/CDP 探针因页面卡顿、超时（如 `SUBMIT_FAIL`）或浏览器窗口丢失等异常中断，轮询进程**必须具备捕获异常并自动重新拉起（Auto-Relaunch）的自愈机制**，严禁让盯盘静默挂起。
    - **快照续接与防重发**：重新拉起后，须比对上一次获取的 `lastUserMessageId` 或 DOM 文本快照平滑续接，不得重复提交已发送的 Prompt。
13. **【文件变更核验与快照回滚契约 (Change Verification & Snapshot Rollback Contract) - P0】**：
    - **任务前快照与清单基线**：每个任务开始前，本地 Agent（`orchestrate.py`）必须先给工作目录做完整快照，并记录文件清单（相对路径 → sha256；>20MB 只记大小；忽略 `.git`/`node_modules`/`__pycache__` 等）。
    - **变更差异即落盘证据**：GPT 给出 `TEST:` 指令后，编排器先比对清单差异（新增/修改/删除）；零变更直接判 BLOCKED（GPT 声称完成但未落盘），有变更才把变更摘要随测试结果一并回传 GPT 审查。
    - **回滚路径**：任何一轮改坏，可用 `orchestrate.py restore --name <项目> --snapshot <快照目录>` 从任务前快照恢复（覆盖快照内文件；快照后新增文件保留并提示人工核对）。
14. **【高管经营内参通用文案与零代码黑话铁律 (Executive Briefing & Zero Jargon Contract) - P0】**：
    - **高管通用视角**：全系统与 AI 交互展示模板统一采用**“高管经营内参”**（如 `**【高管经营内参 · 集团概览】**`），禁止硬编码专属职务字样（如“董事长”）。
    - **零代码黑话泄漏**：严禁在面向高管与业务人员的界面及 Copilot 回复中泄漏任何代码层英文黑话（如 `Canonical Facts`、`project_id`、`cockpit/summary`、`analytics_*` 等）。

### 标准调用模式

> **浏览器选择**：macOS 优先 Safari（AppleScript，无需额外启动）；Linux / Windows 或需要 DevTools 集成时用任何 Chromium 内核浏览器（Chrome / Edge / Brave / Arc / Opera 等，统一走 CDP 协议，仅启动时开启 `--remote-debugging-port` 即可）。

> **安装根目录**：示例统一使用 `$SKILL_ROOT`。Antigravity 全局安装时为 `~/.gemini/antigravity/skills/safari-chatgpt-reasoner`；项目级安装时为 `<项目根目录>/.agent/skills/safari-chatgpt-reasoner`。不要继续使用旧的 `~/.gemini/config/skills` 路径。

### 首次使用：必须登记 Custom GPT 入口地址

首次使用本 Skill 前，用户**必须**向本地 Agent 提供已配置隧道插件（可直读写本机目录）的 Custom GPT 入口地址，格式为：

```
https://chatgpt.com/g/g-<GPT-ID>-<GPT-slug>
```

它与当前会话地址 `https://chatgpt.com/c/<conversation-id>` 是两个不同的值：前者唯一确定要继续使用的 Custom GPT，后者仅绑定当前的一次对话。不得从 `/c/...` 地址猜测或反推出入口地址。

这条配置是额度耗尽、长会话交接或页面异常后创建新会话的前提。未提供入口地址时，Agent 只能使用既有 `--target-url` 完成当前会话的单次交互；**不得**自动点击“新对话”或切换到普通 ChatGPT。入口地址不含登录凭据，用户不应提供 Cookie、令牌或密码。

#### Safari 版（AppleScript，无需额外设置）

```bash
# 1. 精确指定 Tab 执行架构规划
python3 "$SKILL_ROOT/scripts/safari_chatgpt.py" \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type plan \
  --prompt "任务目标描述"

# 2. 本地执行报错反馈闭环（带 L1 渐进脱敏与熔断保护）
python3 "$SKILL_ROOT/scripts/safari_chatgpt.py" \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type feedback \
  --prompt "正在执行模块 A 重构" \
  --evidence-file /tmp/pytest_fail.log \
  --level L1 \
  --signature "ALEMBIC_MIGRATION_DUPLICATE_KEY_ERR"

# 3. 大体量证据推荐走文件，避免 argv 超长（E2BIG）
python3 "$SKILL_ROOT/scripts/safari_chatgpt.py" \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type feedback \
  --prompt "需要审计的执行日志" \
  --evidence-file /var/log/agent_run/large.log \
  --level L2

# 4. 仅清空熔断器
python3 "$SKILL_ROOT/scripts/safari_chatgpt.py" \
  --reset-circuit
```

#### Chromium 版（Chrome / Edge / Brave / Arc / Opera 等，CDP 通用）

所有 Chromium 内核浏览器统一使用同一份 `chrome_chatgpt.py`，协议相同、CDP 端口相同，仅可执行文件路径不同。`--browser-name` 参数用于事件 JSONL 的 `browser` 字段，便于下游审计区分来源，**不影响 CDP 连接**。

前置条件：启动目标浏览器并打开 ChatGPT Tab 后，执行：

```bash
# 方式 A：手动指定端口（默认 9222）
# Chrome
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  https://chatgpt.com/c/<conversation-uuid>

# Microsoft Edge
"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  https://chatgpt.com/c/<conversation-uuid>

# Brave
/Applications/Brave\ Browser.app/Contents/MacOS/Brave\ Browser \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  https://chatgpt.com/c/<conversation-uuid>

# Arc
/Applications/Arc.app/Contents/MacOS/Arc \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  https://chatgpt.com/c/<conversation-uuid>

# 方式 B：命令行启动 Chrome（推荐脚本化）
open -a "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --remote-allow-origins=*
# 然后手动导航到目标 ChatGPT 会话页

# Linux 上（任选 chromium 内核）
chromium --remote-debugging-port=9222 --remote-allow-origins=* \
  https://chatgpt.com/c/<conversation-uuid>
# 或
google-chrome --remote-debugging-port=9222 --remote-allow-origins=* \
  https://chatgpt.com/c/<conversation-uuid>
```

Chromium bridge 调用（默认 `browser="chrome"`，Edge/Brave/Arc 用户用 `--browser-name` 标识来源）：

```bash
# 1. 精确指定 Tab 执行架构规划（默认 localhost:9222，browser=chrome）
python3 "$SKILL_ROOT/scripts/chrome_chatgpt.py" \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type plan \
  --prompt "任务目标描述"

# 2. 指定非默认端口 / host
python3 "$SKILL_ROOT/scripts/chrome_chatgpt.py" \
  --chrome-host 127.0.0.1 \
  --chrome-port 9222 \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type feedback \
  --prompt "正在执行模块 A 重构" \
  --evidence-file /tmp/pytest_fail.log \
  --level L1 \
  --signature "ALEMBIC_MIGRATION_DUPLICATE_KEY_ERR"

# 3. Edge 用户：bridge 不变，仅用 --browser-name 标记事件来源
python3 "$SKILL_ROOT/scripts/chrome_chatgpt.py" \
  --browser-name edge \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type plan \
  --prompt "用 Edge 调用"

# 4. 大体量证据走文件
python3 "$SKILL_ROOT/scripts/chrome_chatgpt.py" \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type feedback \
  --prompt "需要审计的执行日志" \
  --evidence-file /var/log/agent_run/large.log \
  --level L2

# 5. 仅清空 Chromium 熔断器（独立状态文件，不与 Safari 共享）
python3 "$SKILL_ROOT/scripts/chrome_chatgpt.py" \
  --reset-circuit
```

> **注意**：
> - Safari 与 Chromium CDP 熔断器使用**独立**的状态文件（`/tmp/safari_chatgpt_circuit_breaker.json`、`/tmp/chrome_chatgpt_circuit_breaker.json`），互不影响。
> - **不要同时在 9222 端口启两个 Chromium 实例**：CDP 端口冲突会让 `/json/list` 返回错乱 Tab。建议 Edge/Brave 用户把端口改成 9223，并在调用时 `--chrome-port 9223`。

#### Windows 版（Node.js 原生 CDP 驱动，0 扩展 · 0 npm 依赖）

> **中文 Windows 编码**：批处理文件统一使用 UTF-8 与 `chcp 65001`。Bridge 读取提示、证据文件及测试输出时依次支持 UTF-8（含 BOM）、UTF-16LE 和 GB18030/GBK 回退，避免中文日志变成乱码。

前置条件：双击运行项目根目录下的 `start_edge_cdp.bat`（推荐）或 `start_chrome_cdp.bat`，在弹出的窗口中登录 ChatGPT 并打开目标会话。

Node.js Bridge 标准调用方法：

```powershell
# 1. 执行架构规划与 Prompt 发送
node "$SKILL_ROOT\scripts\chrome_chatgpt.js" `
  --target-url "https://chatgpt.com/c/6a9f7b81-0bcc-83e9-a4c1-036d110e1665" `
  --type plan `
  --prompt "任务目标描述"

# 2. 本地执行测试日志反馈闭环（带 L1 渐进脱敏与熔断保护）
node "$SKILL_ROOT/scripts/chrome_chatgpt.js" \
  --target-url "https://chatgpt.com/c/6a9f7b81-0bcc-83e9-a4c1-036d110e1665" \
  --type feedback \
  --prompt "正在执行模块 A 重构" \
  --evidence-file /tmp/pytest_fail.log \
  --level L1 \
  --signature "ALEMBIC_MIGRATION_DUPLICATE_KEY_ERR"

# 3. 指定 Edge 或非默认端口 / host
node "$SKILL_ROOT/scripts/chrome_chatgpt.js" \
  --browser-name edge \
  --chrome-host 127.0.0.1 \
  --chrome-port 9222 \
  --target-url "https://chatgpt.com/c/6a9f7b81-0bcc-83e9-a4c1-036d110e1665" \
  --type plan \
  --prompt "用 Edge 调用"

# 4. 仅清空熔断器
node "$SKILL_ROOT/scripts/chrome_chatgpt.js" \
  --reset-circuit
```

> **注意**：
> - Windows 官方推荐使用纯原生 Node.js CDP 驱动引擎（`chrome_chatgpt.js`），0 个 npm 依赖包，0 个浏览器扩展插件。
> - 使用前双击 `start_edge_cdp.bat`（或 `start_chrome_cdp.bat`）即可自动以隔离数据目录秒开 9222 端口，绝不影响日常浏览器。

### 下游消费规范

**重定向 answer 到文件**（推荐，Safari 与 Chrome 共用）：

```bash
# Safari
python3 .../safari_chatgpt.py --target-url "..." --prompt "..." >answer.txt 2>events.jsonl

# Chrome
python3 .../chrome_chatgpt.py --target-url "..." --prompt "..." >answer.txt 2>events.jsonl

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

1. 首次配置时登记 Custom GPT 入口地址 `https://chatgpt.com/g/g-...`；额度耗尽或需交接时，只能由该入口启动新会话，不能用旧 `/c/<uuid>` 猜测目标 GPT。
2. Execution Plane 打开该 Custom GPT 的新会话页（或调用任何外部手段拿到最终 `/c/<uuid>`）。
3. 把最终 URL 通过 `--target-url` 传入本桥。
4. 由本桥以 Hard Binding 接管该会话的全部交互。

这样 Hard Tab Binding、Custom GPT 身份和"主动切换会话 URL" 不再存在结构性冲突。

---

## Orchestrator：端到端任务编排器

`scripts/orchestrate.py` 在 bridge 之上构建了完整的任务闭环。

### 架构

```
开发者需求
    │
    ▼
orchestrate.py init ────→ GPT-5.6 生成方案（plan）
    │
    ▼
orchestrate.py refine（可选，用户反复核对）
    │
    ▼
orchestrate.py lock ────→ 解析为任务列表（仅本地状态）
    │
    ▼
orchestrate.py run-task × N ────→ 每个任务的闭环：
    │
    ├── 本地 Agent 任务前快照 + 文件清单基线
    ├── GPT 通过隧道插件直读写本机文件（task-code）
    ├── 本地 Agent 核对文件变更清单（零变更判 BLOCKED）
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
| `task-code` | 单任务代码生成与落盘 | 代码由 Custom GPT 直接通过隧道插件读写本机工作目录中的文件完成。在对话回复中：**5. 只测试命令，不要输出代码，不要写说明文字。** 测试命令以 `TEST:` 标记，预期结果以 `EXPECTED:` 标记。 |
| `task-review` | 代码 + 测试结果审查 | 只输出 `APPROVED` / `NEEDS_FIX(原因)` / `BLOCKED(原因)` |

【输出格式要求】（严格遵守）：
1. 代码直接使用隧道插件写入本机工作目录。
2. 禁止在对话回复中粘贴大段代码，避免冗余和格式截断。
3. 测试命令用以下格式（放在单独的 bash 块中）：
   `TEST: <实际命令>`
   `EXPECTED: <预期结果描述>`
4. 如果任务涉及多文件，请按依赖顺序排列。
5. 只测试命令，不要输出代码，不要写说明文字。

### Orchestrator 子命令速查

```bash
# 初始化项目（生成初始方案）
python3 scripts/orchestrate.py init \
  --name my-migration \
  --requirement "把 Flask 认证迁移到 FastAPI + JWT" \
  --target-url "https://chatgpt.com/c/xxx" \
  --cwd /path/to/project \
  --browser safari

# Chromium 家族把 --browser 换成 chrome / edge / brave / arc / chromium；
# Windows 上编排器对非 Safari 浏览器自动改走 Node 原生 CDP 驱动（chrome_chatgpt.js）。

# 方案审核与修改（可多次，直到满意）
python3 scripts/orchestrate.py refine \
  --name my-migration \
  --feedback "第3步风险太高，能不能先做兼容性 shim"

# 锁定方案（解析为任务列表，仅本地状态）
python3 scripts/orchestrate.py lock --name my-migration

# 执行单个任务（交互模式：每步等用户）
python3 scripts/orchestrate.py run-task --name my-migration --task-id 1

# 执行所有 pending 任务（全自动）
python3 scripts/orchestrate.py run-task --name my-migration --autonomous

# 全自动 + 任务失败也继续下一个
python3 scripts/orchestrate.py run-task --name my-migration \
  --autonomous --continue-on-fail

# 查看状态
python3 scripts/orchestrate.py status --name my-migration

# 从快照回滚工作目录（快照路径见 status 输出）
python3 scripts/orchestrate.py restore --name my-migration --snapshot <快照目录>
```

### 状态文件

- `~/.antigravity/orchestrator/<name>.state.json` — 完整项目状态
- `~/.antigravity/orchestrator/<name>/PLAN.md` — 当前方案（可编辑）
- `~/.antigravity/orchestrator/<name>/TASKS.json` — 解析后的任务列表

每个任务状态：
- `pending` → `coding` → `testing` → `approved` / `failed` / `blocked`

### 单任务闭环流程（隧道直读写与本地验收）

```
┌─────────────────────────────────────────────────────────┐
│ 1. Safari Custom GPT (配隧道插件) 直读写本机文件         │
│    - 先由本地 Agent 做任务前快照并记录文件清单基线       │
│    - GPT 直接调用隧道插件读写工作目录中的文件完成修改    │
│    - 给出本地测试命令（如 pytest / npm test）             │
│    - 说明预期测试结果（如 "all tests pass", "exit 0"）    │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 2. 本地 Agent 核对文件变更清单                           │
│    - 比对任务前后的文件清单（sha256）差异                │
│    - 零变更直接判 BLOCKED；有变更生成变更摘要            │
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
│    - NEEDS_FIX：测试失败，Custom GPT 再次原地修复 🔧     │
│    - BLOCKED：环境/依赖受阻，需人工介入 🚫               │
└─────────────────────────────────────────────────────────┘

【自动修复循环】
NEEDS_FIX → Custom GPT 原地重新修改 → Agent 核对变更 + 测试 → GPT 审查
最多 3 轮（可配置），避免无限循环
```

每个任务状态：
- `pending` → `coding` → `testing` → `approved` / `failed` / `blocked`
- 修复中：`fixing` → `coding` → `testing` → 循环或完成

### 关键设计原则

- **第一铁律（隧道直读写 + 本地核对验收）**：Safari 上的 Custom GPT 配备隧道插件直接读写本机工作目录；本地 Agent 以文件清单差异核对落盘并在真实环境运行测试，完成闭环验收。
- **测试驱动闭环**：Custom GPT 必须给出测试验证命令，Agent 跑测试后把真实结果反馈给 GPT，由 GPT 根据实际测试日志决定通过/修复/阻塞，避免主观判断。
- **强制测试命令**：Custom GPT 交付改动时必须提供至少一个测试命令，确保每次改动都可被本地物理验证。
- **自动修复机制**：测试失败时，Custom GPT 会根据真实报错信息直接原地修复，最多尝试 3 轮。每轮都是完整的“原地修改 ➔ 变更核对 ➔ 本地测试 ➔ 审查裁决”闭环；每轮开始前自动快照，改坏可 `restore` 回滚。
- **职责边界清晰**：Custom GPT 负责高阶认知、方案与本机文件改动，本地 Agent 负责快照、变更核对、本地执行与环境验证。推演出错找 GPT，落地与环境出错找 Agent。
- **可恢复**：所有状态持久化到 `~/.antigravity/orchestrator/`，中断后可随时恢复。
- **长会话平滑交接 (Handoff Protocol)**：当对话历史过长导致 WebKit/页面渲染负载增加时，执行 `旧会话生成交接摘要 ➔ 换新会话 URL 注入继续`，兼顾 100% 上下文继承与极致流畅度。
- **可信证据**：所有 bridge 调用走事件 JSONL，stderr 记录每次 GPT 请求/响应的基线快照。
