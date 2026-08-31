---
name: safari-chatgpt-reasoner
description: 双层混合 Agent 系统：以 Safari/Chrome ChatGPT 网页端为云端认知控制面（High Intelligence / Zero Privilege），以本地 Agent 为确定性数据面（High Privilege / Low Ambiguity）。v4.1 起强制执行 Tab 精确绑定 + 跨进程事务锁 + 精确 user-message identity + assistant message-id 稳定阶段，证据完整性真正可机验。浏览器通道可选 Safari（AppleScript）或 Chrome（CDP）；所有 P0/P1/P2 契约完全等价。
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
