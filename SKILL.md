---
name: safari-chatgpt-reasoner
description: 双层混合 Agent 系统：以 Safari/Chrome ChatGPT 网页端为云端认知控制面（High Intelligence / Zero Privilege），以任意本地桌面 Agent 为确定性数据面（High Privilege / Low Ambiguity）。v4.1 起强制执行 Tab 精确绑定 + 跨进程事务锁 + 精确 user-message identity + assistant message-id 稳定阶段，证据完整性真正可机验。含端到端 Orchestrator（scripts/orchestrate.py）：支持任意桌面 Agent 作为执行端，GPT-5.6 SOL 生成方案和代码，GitHub 留底，每任务闭环可自动修复。
---

# Safari / Chrome ChatGPT Reasoner Skill (v4.1 Evidence-Integrity)

### 架构原则与契约 (Architecture Contracts)
1. **三权分立与单向依赖**：
   - **Reasoning Plane（ChatGPT Web）**：高阶架构推演、故障归因与规划（零系统读写权限）。
   - **Execution Plane（本地 Agent）**：文件原子修改、依赖安装、环境构建（高权限确定性操作）。
   - **Verification Plane（Evidence Store）**：独立以 exit code、事件 JSONL、git diff 为客观事实准绳。
2. **URL + Transaction + User + Assistant 四重身份绑定 (P0)**：
   - 调用方必须通过 `--target-url` 显式指定 ChatGPT 会话 URL（仅接受 `https://chatgpt.com` / `https://www.chatgpt.com`）。
   - 同一 `target_url` 在任意时刻只允许一个 RPC（`TargetTabLock`，`fcntl.flock(LOCK_EX|LOCK_NB)`）。
   - 提交验证不再用"消息数量 +2"启发式；改在 JS 端规范化比对 last user message 是否 === expected prompt。
   - 助手稳定阶段只读 `data-message-id` 唯一定位的目标 turn；若节点消失/身份变化即视为证据失效（不再依赖 totalCount）。
3. **渐进式证据等级 (L0 – L3)**：
   - L0：**不传 Evidence 正文**，仅传调用上下文与 git 状态摘要。
   - L1：折叠中段，保留头 5 行 + 尾 35 行。
   - L2：保留尾部 80 行。
   - L3：全文（建议改用 `--evidence-file`）。
4. **结构化退出码 (Verification Plane 唯一判据)**：
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
5. **时效与 deadline (P0-4)**：
   - `--timeout` 是 monotonic 绝对 deadline，覆盖 baseline → 注入 → 提交 → 新回合 → 稳定 全过程。
   - 任何阶段用 `min(phase_budget, remaining_deadline)` 切片；超时即返回当前最新事实。
6. **写操作 fail-fast (P1-4)**：
   - 本版本**不再**自动重试 Safari 写操作（`inject` / `send` 非幂等）。
   - 失败立即抛错并退出，宁可让调用方显式重试，也不要猜测。
7. **熔断器 (P1-1 / P1-2 / P1-3)**：
   - 滑动时间戳窗口（默认 1h 内 ≤3 次同签名失败即熔断）。
   - 每次 read-modify-write 自动 prune 过期签名；空 entry 直接删除，state JSON 不会无限增长。
   - `reset_circuit_breaker` 自身持锁，并可通过 `--reset-circuit` 显式调用。
8. **双层职责绝对隔离与防抢跑铁律 (Strict Division of Labor & Anti-Preemption Constraint) [P0-核心]**：
   - **GPT 独占方案与代码生成权**：所有架构方案、实施任务分解、业务代码块（必须带 `filepath: <path>` 标记）与测试命令，必须由 ChatGPT 网页端深度推演并完整生成。
   - **本地 Agent 严禁自主编写业务代码**：本地 Agent 绝对禁止越俎代庖自己编写业务代码或擅自更改方案。本地 Agent 的职责严格受限于：采集上下文/报错堆栈 ➔ 提交给 GPT ➔ 审查并解析 GPT 输出的代码 ➔ 原子落盘写入文件 ➔ 运行测试 ➔ 将测试结果与 exit code 忠实回传给 GPT 审查（`task-review`）。
   - **绝对防抢跑纪律**：当 ChatGPT 处于深度思考（Reasoning）、工具调用或流式生成时，本地 Agent 必须耐心等待完整输出，绝对不得以“耗时较长”或“为了提速”为借口抢跑自行编写代码。
   - **最终闭环判据**：每一个任务的闭环必须由 GPT 在 `task-review` 阶段审查测试日志并显式输出 `APPROVED` 裁决，本地 Agent 方可进入下一任务或交付。

### 标准调用模式

> **浏览器选择**：macOS 优先 Safari（AppleScript，无需额外启动）；Linux / Windows 或需要 DevTools 集成时用任何 Chromium 内核浏览器（Chrome / Edge / Brave / Arc / Opera 等，统一走 CDP 协议，仅启动时开启 `--remote-debugging-port` 即可）。

#### Safari 版（AppleScript，无需额外设置）

```bash
# 1. 精确指定 Tab 执行架构规划
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/safari_chatgpt.py \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type plan \
  --prompt "任务目标描述"

# 2. 本地执行报错反馈闭环（带 L1 渐进脱敏与熔断保护）
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/safari_chatgpt.py \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type feedback \
  --prompt "正在执行模块 A 重构" \
  --evidence-file /tmp/pytest_fail.log \
  --level L1 \
  --signature "ALEMBIC_MIGRATION_DUPLICATE_KEY_ERR"

# 3. 大体量证据推荐走文件，避免 argv 超长（E2BIG）
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/safari_chatgpt.py \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type feedback \
  --prompt "需要审计的执行日志" \
  --evidence-file /var/log/agent_run/large.log \
  --level L2

# 4. 仅清空熔断器
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/safari_chatgpt.py \
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
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/chrome_chatgpt.py \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type plan \
  --prompt "任务目标描述"

# 2. 指定非默认端口 / host
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/chrome_chatgpt.py \
  --chrome-host 127.0.0.1 \
  --chrome-port 9222 \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type feedback \
  --prompt "正在执行模块 A 重构" \
  --evidence-file /tmp/pytest_fail.log \
  --level L1 \
  --signature "ALEMBIC_MIGRATION_DUPLICATE_KEY_ERR"

# 3. Edge 用户：bridge 不变，仅用 --browser-name 标记事件来源
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/chrome_chatgpt.py \
  --browser-name edge \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type plan \
  --prompt "用 Edge 调用"

# 4. 大体量证据走文件
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/chrome_chatgpt.py \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type feedback \
  --prompt "需要审计的执行日志" \
  --evidence-file /var/log/agent_run/large.log \
  --level L2

# 5. 仅清空 Chromium 熔断器（独立状态文件，不与 Safari 共享）
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/chrome_chatgpt.py \
  --reset-circuit
```

> **注意**：
> - Safari 与 Chromium 熔断器使用**独立**的状态文件（`/tmp/safari_chatgpt_circuit_breaker.json` vs `/tmp/chrome_chatgpt_circuit_breaker.json`），互不影响。
> - **不要同时在 9222 端口启两个 Chromium 实例**：CDP 端口冲突会让 `/json/list` 返回错乱 Tab。建议 Edge/Brave 用户把端口改成 9223，并在调用时 `--chrome-port 9223`。

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

1. Execution Plane 打开目标 ChatGPT 会话页（或调用任何外部手段拿到最终 `/c/<uuid>`）。
2. 把最终 URL 通过 `--target-url` 传入本桥。
3. 由本桥以 Hard Binding 接管该会话的全部交互。

这样 Hard Tab Binding 与"主动切换会话 URL" 不再存在结构性冲突。

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
orchestrate.py lock ────→ 解析为任务列表，推送到 GitHub
    │
    ▼
orchestrate.py run-task × N ────→ 每个任务的闭环：
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

### 新增 `--type` 协议

| type | 用途 | GPT 输出要求 |
|---|---|---|
| `task-code` | 单任务代码生成 | 每个文件以 `filepath: <path>` 标记；测试命令以 `TEST:` 标记 |
| `task-review` | 代码 + 测试结果审查 | 只输出 `APPROVED` / `NEEDS_FIX(原因)` / `BLOCKED(原因)` |

### Orchestrator 子命令速查

```bash
# 初始化项目（生成初始方案）
python3 scripts/orchestrate.py init \
  --name my-migration \
  --requirement "把 Flask 认证迁移到 FastAPI + JWT" \
  --target-url "https://chatgpt.com/c/xxx" \
  --repo git@github.com:xxx/yyy.git \
  --cwd /path/to/repo \
  --browser safari

# 方案审核与修改（可多次，直到满意）
python3 scripts/orchestrate.py refine \
  --name my-migration \
  --feedback "第3步风险太高，能不能先做兼容性 shim"

# 锁定方案（解析为任务列表，提交到 GitHub）
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
```

### 状态文件

- `~/.antigravity/orchestrator/<name>.state.json` — 完整项目状态
- `~/.antigravity/orchestrator/<name>/PLAN.md` — 当前方案（可编辑）
- `~/.antigravity/orchestrator/<name>/TASKS.json` — 解析后的任务列表

每个任务状态：
- `pending` → `coding` → `testing` → `approved` / `failed` / `blocked`

### 单任务闭环流程（优化版）

```
┌─────────────────────────────────────────────────────────┐
│ 1. GPT 写代码                                            │
│    - 输出代码文件（标注 filepath）                        │
│    - 给出测试命令（必须，如 npm test / pytest）          │
│    - 说明预期结果（如 "all tests pass", "exit 0"）      │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Agent 写文件 + 自动推送 GitHub                        │
│    - 解析代码块并写入本地文件                            │
│    - git add + commit + push                             │
│    - 记录 commit SHA                                     │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 3. Agent 执行测试                                        │
│    - 运行 GPT 提供的测试命令                             │
│    - 收集 stdout / stderr / exit code                   │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Agent 反馈测试结果给 GPT                              │
│    - 测试命令 + 实际输出                                 │
│    - 通过 / 失败状态                                     │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 5. GPT 审查并决定下一步                                  │
│    - APPROVED：测试通过，任务完成 ✅                     │
│    - NEEDS_FIX：测试失败，给修复建议 → 自动修复循环 🔧  │
│    - BLOCKED：无法自动解决，需人工介入 🚫                │
└─────────────────────────────────────────────────────────┘

【自动修复循环】
NEEDS_FIX → GPT 重写代码 → Agent 推送 + 测试 → GPT 审查
最多 3 轮（可配置），避免无限循环
```

每个任务状态：
- `pending` → `coding` → `testing` → `approved` / `failed` / `blocked`
- 修复中：`fixing` → `testing` → 循环或完成

### 关键设计原则

- **测试驱动闭环**：GPT 必须给出测试命令，Agent 跑测试后把真实结果反馈给 GPT，GPT 根据实际测试结果决定通过/修复/阻塞，避免主观判断。
- **自动推送 GitHub**：每次代码改动（包括修复）都自动 commit + push，GitHub 是最终事实来源，可追溯、可回滚。
- **强制测试命令**：GPT 写代码时必须提供至少一个测试命令，如果忘记提供会自动要求补充，确保每次改动都可验证。
- **自动修复机制**：测试失败时，GPT 会根据错误信息自动修复，最多尝试 3 轮。每轮都是完整的"写代码→推送→测试→审查"循环。
- **单向依赖**：GPT 只能给方案和代码，本地 Agent 负责执行和测试。责任边界清晰：推演出错找 GPT，落地出错找 Agent。
- **可恢复**：所有状态持久化到 `~/.antigravity/orchestrator/`，Ctrl+C 后可直接 `resume`。
- **长会话平滑交接 (Handoff Protocol)**：当对话历史过长导致 WebKit/页面渲染负载增加时，执行 `旧会话生成交接摘要 ➔ 换新会话 URL 注入继续`，兼顾 100% 上下文继承与极致流畅度。
- **可信证据**：所有 bridge 调用走事件 JSONL，stderr 记录每次 GPT 请求/响应的基线快照。

