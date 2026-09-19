---
name: agent-chatgpt-web-bridge
description: 纯自然语言驱动的通用 AI 协作开发桥梁：让任意桌面 AI Agent 直连 ChatGPT 网页版最强大脑，自动规划、自动写代码、自动同步 GitHub，用户无需懂编程与命令行。
---

# 🤖 Desktop Agent × ChatGPT 网页版 × GitHub
### 纯自然语言驱动的通用“双脑”AI 协作开发方案（支持任意桌面 Agent）

> **一句话介绍**：你不需要懂编程、不需要会写 Python 脚本，更不需要在终端敲复杂命令——**只需在聊天框里用平常说话的方式说出你的想法**，你的桌面 AI 助手（无论使用哪种桌面 Agent）就会自动连接你的 ChatGPT 网页版最强大脑，帮你制定方案、编写代码、测试验证并安全备份到 GitHub！

---

## 💡 这套方案能帮你做什么？

过去做项目或开发功能时，经常遇到的困扰：
- 🤯 **思路卡壳 / 架构复杂**：遇到疑难问题不知道怎么拆解，需要顶级的 AI 深度思考模型来指导。
- 😫 **来回复制太痛苦**：在网页版 ChatGPT 和本地桌面编辑器之间来回“复制、粘贴、运行、报错、再复制”，费时费力。
- 😨 **改错了无法挽回**：改了几个文件之后项目跑不通了，找不到原来的代码，不知道怎么回滚。

**现在，双 AI 自动协作，全流程自然语言驱动：**

```
           🗣️ 用户（你）：只需自然语言提需求
                     │
                     ▼
       ┌──────────────────────────────┐
       │   任意桌面 AI 助手 (Agent)   │
       └──────────────┬───────────────┘
                      │
         ┌────────────┴────────────┐
         ▼                         ▼
┌───────────────────┐    ┌───────────────────┐
│  ChatGPT 网页端   │    │  本地项目 & GitHub │
│  (云端最强大脑)   │    │  (自动落地的双手) │
├───────────────────┤    ├───────────────────┤
│ 负责深度推演架构   │    │ 自动修改工程文件  │
│ 制定最优实施步骤   │    │ 自动运行测试排错  │
│ 提供精准技术方案   │    │ 自动备份提交记录  │
└───────────────────┘    └───────────────────┘
```

---

## 👥 你的专属“双 AI”团队分工与执行铁律

| 角色 | 核心职责 | 行为边界（严禁越位） | 带来的好处 |
|---|---|---|---|
| 🧠 **ChatGPT 网页版**（云端总架构师） | 负责高强度逻辑推演、方案拆解、**通过 GitHub 工具修改、提交和推送业务代码**、提供测试命令与审核测试结果 | 仅操作用户授权的远程仓库与分支，独占方案与代码修改权 | 借助网页端顶级深度思考与推理能力，保证全局架构与业务代码高质量 |
| ⚡ **本地桌面 AI 助手**（精准验收执行端） | 负责提取环境上下文、`git pull --ff-only` 同步远端提交、运行测试命令、将测试日志回传 GPT 审查 | **严禁自行编写、落盘、提交或推送业务代码**；工作区有未提交改动时必须停止同步 | 用真实环境提供可复核证据，杜绝本地抢跑与覆盖远端提交 |
| 🛡️ **GitHub 仓库**（版本安全管家） | 自动记录每一次变动，保留完整历史 | 任何一步改动都清清楚楚，改错了随时一键撤销 | 随时可回滚，进度透明可追溯 |

> ⚠️ **核心纪律**：本地桌面 Agent 绝对禁止越俎代庖自己编写、落盘、提交或推送业务代码；必须严格等待云端 ChatGPT 通过 GitHub 完成推送，然后 fast-forward 拉取、运行测试并回传审查，直到 ChatGPT 裁决 `APPROVED` 为止。

---

## 安装到 Antigravity IDE

本项目是 **GitHub 分支发布** 的 Skill：macOS 使用 `macos` 分支，Windows 使用纯 Node.js 的 `windows` 分支；`main` 仅提供公开项目说明。安装和更新都需要 Git。请在下列两种范围中选一种，不能同时安装同名副本。

### 全局安装（所有项目可用）

macOS：

```bash
mkdir -p ~/.gemini/antigravity/skills
git clone --branch macos --single-branch \
  https://github.com/yvochehk-oss/Antigravity2gptweb.git \
  ~/.gemini/antigravity/skills/safari-chatgpt-reasoner
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.gemini\antigravity\skills" | Out-Null
git clone --branch windows --single-branch https://github.com/yvochehk-oss/Antigravity2gptweb.git "$env:USERPROFILE\.gemini\antigravity\skills\safari-chatgpt-reasoner"
```

### 项目级安装（推荐用于团队项目）

在项目根目录执行。该 Skill 会随项目的 `.agent/` 配置一起被 Antigravity 识别：

```bash
mkdir -p .agent/skills
git clone --branch macos --single-branch \
  https://github.com/yvochehk-oss/Antigravity2gptweb.git \
  .agent/skills/safari-chatgpt-reasoner
```

Windows 用户把上面两条命令中的 `macos` 改为 `windows`。安装或更新后请新开一个 Antigravity 对话。

### 更新

```bash
git -C ~/.gemini/antigravity/skills/safari-chatgpt-reasoner pull --ff-only
```

项目级安装时，把上述路径改为 `.agent/skills/safari-chatgpt-reasoner`。

## GitHub 前置条件

这个 Skill 的协作闭环依赖 GitHub，而不只是把 ChatGPT 网页自动化：

1. 用户需要有 GitHub 账户，以及已存在或新建的目标仓库。
2. Custom GPT 必须已连接并获授权操作该仓库的 GitHub 工具；只授予本任务需要的仓库权限。
3. 本地项目必须是同一远程仓库的工作副本，且在开始前工作区干净，方便执行 `git pull --ff-only`。

没有 GitHub 授权时，可以使用桥接器进行网页对话，但不能使用本项目承诺的“远端提交 → 本地拉取 → 测试证据 → GPT 审查”闭环。

---

## macOS：BrowserSkill Profile 选择

macOS 除 Safari bridge 外，也支持 Tencent BrowserSkill（`bsk`）驱动 Chrome / Edge 等 Chromium 浏览器。推荐把专门用于 ChatGPT 的浏览器 Profile 安装并启用 BrowserSkill 扩展，然后由 Antigravity 显式选择该 Profile。

BrowserSkill 不接受 Chrome 的 `--profile-directory` 作为 session 参数；本项目使用 BrowserSkill 自身的 `instance_id / label` 选择机制。每个安装扩展的浏览器 Profile 都会成为一个独立 BrowserSkill 实例。

```bash
# 查看在线 Profile / browser instance
python3 scripts/bsk_chatgpt.py --list-browser-profiles

# 检查某个 Profile
python3 scripts/bsk_chatgpt.py --check-env --browser-profile "GPT专用"

# 使用指定 Profile 的独立 Agent Window
python3 scripts/bsk_chatgpt.py \
  --browser-profile "GPT专用" \
  --target-url "https://chatgpt.com/c/<conversation-id>" \
  --type plan \
  --prompt "任务目标描述"
```

默认行为是独立 Agent Window + `--no-focus`，不会主动借用用户主窗口里的 ChatGPT 标签页。确有页面级临时状态必须复用时，才显式增加 `--borrow`。

当多个 BrowserSkill 实例同时在线时，必须通过 `--browser-profile <instance_id|唯一label>` 指定目标；系统不会随机选择。Orchestrator 初始化时也可以直接固定：

```bash
python3 scripts/orchestrate.py init \
  --name my-project \
  --requirement "项目需求" \
  --target-url "https://chatgpt.com/c/<conversation-id>" \
  --repo git@github.com:owner/repo.git \
  --cwd /path/to/repo \
  --browser bsk \
  --browser-profile "GPT专用"
```

这个选择会保存在项目状态中，后续 plan、task-code、task-review 与自动修复都会继续使用同一个 BrowserSkill Profile。

---

## 🚀 首次配置与启动（纯自然语言）

完成安装和 GitHub 前置条件后，再进行以下三步：

### 第 1 步：登记 Custom GPT 入口地址（首次必填）
先打开你要协作使用的 Custom GPT，并把它的入口地址提供给本地 Agent，格式为：`https://chatgpt.com/g/g-<GPT-ID>-<GPT-slug>`。

这不是当前对话的 `/c/...` 地址。入口地址用于额度耗尽、长会话交接或页面异常时创建同一个 Custom GPT 的新会话；仅有 `/c/...` 时，系统不得自动点“新对话”，以免误进普通 ChatGPT。

### 第 2 步：浏览器打开该 Custom GPT 的一个对话
在上述 Custom GPT 中开启对话，复制最终的会话网址（例如：`https://chatgpt.com/c/xxxx-xxxx`）。

### 第 3 步：在聊天框用普通话告诉 AI 你的需求
直接向桌面 AI 助手发送指令，像跟同事交流一样自然：

> 💬 **你可以这样对 AI ### 关键特性

- **第一铁律（远端直连修改 + 本地拉取验收）**：Safari 上的 Custom GPT 配备 GitHub 工具直接在 GitHub 上完成代码修改和推送，本地 Agent 负责 `git pull` 并进行真实环境测试验收。
- **对话极简轻量（零冗余代码粘贴）**：Custom GPT 直接在 GitHub 仓库落地修改，对话界面中**绝不输出大段冗余代码**，仅输出测试验证指令（`TEST:` 与 `EXPECTED:`），避免截断和页面卡顿。
- **强制测试命令**：Custom GPT 每次提交修改都必须提供测试验证命令，确保每一步改动都可被物理验证。
- **测试驱动闭环**：本地 Agent 跑完测试后，Custom GPT 根据实际测试日志客观决定是否通过，避免主观猜测。
- **自动修复机制**：测试失败时，Custom GPT 会根据真实报错信息直接远程修复并重新推送，最多尝试 3 次。
- **人工介入保护**：遇到无法自动解决的问题（如外部环境依赖、业务歧义），会标记 BLOCKED 等待人工处理。

---

## 典型场景

### 场景 1：多任务串行执行
> 💬 **用户指令**："我想做一个 Todo API，有增删改查四个功能。请让 ChatGPT 拆成 4 个任务，每个任务远程提交并测试通过后再做下一个。"

**流程**：Custom GPT 远程提交 + 输出测试命令 → 本地 Agent `git pull` + 测试 → Custom GPT 审查通过 ✅ → 下一个任务

### 场景 2：项目重构
> 💬 **用户指令**："我想把项目的旧接口改造为更现代的架构。请让 ChatGPT 评估风险并制定 3 步改造计划，每一步确保测试通过后再做下一步。"

**流程**：每一步都是 Custom GPT 远程推送 → 本地 Agent `git pull` + 测试 → Custom GPT 审查 → 验证通过后进入下一步

### 场景 3：自动修复 Bug
> 💬 **用户指令**："项目在运行到某一步时会报错。请把报错信息提交给 ChatGPT 分析原因，并在远端自动修复。"

**流程**：Custom GPT 分析原因并远程推送修复 → 本地 Agent `git pull` + 测试 → 失败则反馈报错由 Custom GPT 再次修复 → 循环最多 3 次直到通过 ✅

### 场景 4：中途调整方向
> 💬 **用户指令**：“刚才第 2 步的设计我感觉不太合适，请让 ChatGPT 换一种更轻量的实现方式，然后重新推送修改。”

**流程**：用户随时可以调整方案，Custom GPT 重新远程推送代码 → 本地 Agent `git pull` + 测试 → Custom GPT 审查 → 验证通过后继续（第一铁律：GitHub 直连修改 + 本地验收闭环）

### 完整的任务执行流程

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Safari Custom GPT (配备 GitHub 工具) 远端修改与推送      │
│    - 直接调用 GitHub 工具对远程仓库执行文件修改与推送        │
│    - 给出本地测试命令（如 pytest / npm test）                │
│    - 说明预期测试结果（如 "all tests pass", "exit 0"）       │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. 本地 Agent 执行 git pull 同步代码                         │
│    - 收到推送后在本地自动执行 git pull                       │
│    - 确认工作区分支与最新 commit 同步完毕                    │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. 本地 Agent 执行本地测试                                   │
│    - 运行 Custom GPT 指定的测试命令                          │
│    - 收集 stdout / stderr / exit code                       │
│    - 真实捕获本地执行环境中的运行状态                        │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. 本地 Agent 反馈测试结果给 Custom GPT                      │
│    - 测试命令 + 实际运行日志与报错堆栈                       │
│    - 通过 / 失败状态（exit code）                           │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Custom GPT 审查并裁决下一步                               │
│    - APPROVED：测试通过，任务完成 ✅                         │
│    - NEEDS_FIX：测试失败，Custom GPT 直接远程修改并推送修复 🔧│
│    - BLOCKED：环境/依赖受阻，需要人工介入 🚫                 │
└─────────────────────────────────────────────────────────────┘

【自动修复循环】
如果 Custom GPT 裁决为 NEEDS_FIX，会自动进入修复循环：
1. Custom GPT 根据本地测试失败原因直接在 GitHub 远端重新修改并 push
2. 本地 Agent 再次 git pull 同步最新修复代码
3. 本地 Agent 重新运行本地测试
4. 本地 Agent 把新测试结果反馈给 Custom GPT
5. Custom GPT 再次审查 → APPROVED / NEEDS_FIX / BLOCKED
6. 最多尝试 3 次，避免无限循环
```

### 关键特性

- **第一铁律（远端直连修改 + 本地拉取验收）**：Safari 上的 Custom GPT 配备 GitHub 工具直接在 GitHub 上完成代码修改和推送，本地 Agent 负责 `git pull` 并进行真实环境测试验收。
- **强制测试命令**：Custom GPT 每次提交修改都必须提供测试验证命令，确保每一步改动都可被物理验证。
- **测试驱动闭环**：本地 Agent 跑完测试后，Custom GPT 根据实际测试日志客观决定是否通过，避免主观猜测。
- **自动修复机制**：测试失败时，Custom GPT 会根据真实报错信息直接远程修复并重新推送，最多尝试 3 次。
- **人工介入保护**：遇到无法自动解决的问题（如外部环境依赖、业务歧义），会标记 BLOCKED 等待人工处理。


### 场景 5：复杂执行计划单步流式推进 (Step-by-Step Plan Execution & Approval)
> 💬 **用户指令**：“按照 implementation_plan.md，一步一步地完成”

**流程**：
1. **严格单条推进**：本地 Agent 严禁打包批量推送整个方案，必须按照计划一次仅提取当前单条原子任务发给 Custom GPT；
2. **GPT 输出远程提交与检验命令**：Custom GPT 在 GitHub 远端落地修改与推送，并显式输出本地检验命令（`TEST: ...` / `EXPECTED: ...`）；
3. **本地 Agent 真实环境跑验证**：本地 Agent 执行 `git pull` 同步最新代码，严格跑完验证命令，收集完整日志与退出码并反馈给 Custom GPT；
4. **GPT 审查同意后推进下一条**：Custom GPT 审查测试证据并显式输出 `APPROVED` 裁决后，本地 Agent 方可取出下一条任务推进，步步为营，绝对闭环！

---

## 典型场景

### 场景 1：多任务串行执行
> 💬 **用户指令**："我想做一个 Todo API，有增删改查四个功能。请让 ChatGPT 拆成 4 个任务，每个任务写完自动测试通过后再做下一个。"

**流程**：GPT 写代码 + 给测试命令 → Agent 测试 → GPT 审查通过 ✅ → 自动推送 GitHub → 下一个任务

### 场景 2：项目重构
> 💬 **用户指令**："我想把项目的旧接口改造为更现代的架构。请让 ChatGPT 评估风险并制定 3 步改造计划，每一步确保测试通过后再做下一步。"

**流程**：每一步都是 GPT 写代码 → Agent 测试 → GPT 审查 → 通过后推送 GitHub → 才进入下一步

### 场景 3：自动修复 Bug
> 💬 **用户指令**："项目在运行到某一步时会报错。请把报错信息提交给 ChatGPT 分析原因，并在本地自动修复。"

**流程**：GPT 分析错误并修复 → Agent 测试 → 失败则 GPT 看到测试结果再修复 → 循环最多 3 次直到通过 ✅

### 场景 4：中途调整方向
> 💬 **用户指令**："刚才第 2 步的设计我感觉不太合适，请让 ChatGPT 换一种更轻量的实现方式，然后重新修改本地代码。"

**流程**：用户随时可以调整方案，GPT 重新生成代码 → Agent 测试 → GPT 审查 → 通过后推送


---

## ⚡ 保持浏览器极速流畅的秘诀：分阶段与“交接摘要（HANDOFF）”

在进行长周期、多步骤的复杂任务时（如大型重构、复杂业务系统开发），单个网页对话如果聊得过长、堆积了大量代码和表格，会导致浏览器（特别是 Safari / WebKit）渲染内存变大而出现卡顿。

### 💡 核心法则：**旧对话 → 生成交接摘要 → 新对话继续**

当一个阶段完成，或者感到网页响应变慢时，**千万不要在超长对话里硬撑**，只需用自然语言让 AI 执行平滑交接：

1. **一句话生成交接**：
   > 💬 **对 AI 说**：“当前阶段已完成，请帮我生成一份【项目交接摘要（HANDOFF）】，总结已完成的工作、关键架构决策和下一步待办。”
2. **重开新对话**：
   - 彻底关闭当前的 ChatGPT 标签页（完全释放浏览器内存）；
   - 在浏览器中新开一个 ChatGPT 对话，复制新的网址链接；
3. **继续无缝推进**：
   > 💬 **对 AI 说**：“这是新的 ChatGPT 链接：`https://chatgpt.com/c/新链接`，请将刚才的交接摘要注入进去，继续进行下一步。”

这样既能**100% 完整继承项目上下文与决策**，又能让浏览器始终保持秒级极速响应！

---

### 📋 标准项目交接模板（HANDOFF Template）

桌面 AI 会自动按此标准化格式生成摘要，保证上下文零丢失：

```markdown
# 📌 项目交接摘要 (Project Handoff)

### 1. 项目背景与当前阶段
- **项目目标**：[简述项目的核心目标]
- **当前所处阶段**：[例如：第 2/5 阶段，核心数据模型与鉴权已完成]
- **GitHub 分支/最新提交**：[当前工作分支与关键 Commit 记录]

### 2. 已锁定的核心架构决策 (Decisions)
- [决策 1：采用 JWT 无状态鉴权方案]
- [决策 2：数据迁移采用平滑过渡中间件]

### 3. 当前代码现状与已修改文件 (Status)
- ✅ `src/auth/jwt.py`：JWT 生成与校验逻辑，测试全部通过
- ✅ `tests/test_auth.py`：单元测试覆盖完毕

### 4. 下一步待办清单 (Next Steps)
- 🔲 步骤 1：开发角色权限中间件
- 🔲 步骤 2：替换旧业务接口的鉴权逻辑

### 5. 遗留注意事项与已知边界 (Notes)
- 注意：保持与老前端的响应头兼容
```

---

## ❓ 常见问题解答（FAQ）

### Q1：支持哪些桌面 AI 助手（Agent）？
**答**：**支持任何具备文件读写与对话能力的桌面 Agent**。
无论是 Antigravity、Cursor、Windsurf、还是其他本地桌面 AI 工具，都可以搭载本 Skill 与 ChatGPT 网页版无缝协作。

### Q2：我完全不会编程 / 不会写 Python，能用吗？
**答**：**完全可以！** 
你不需要写一行代码，也不用在终端运行任何 Python 脚本。所有底层桥接、网页交互和文件修改都由桌面 AI 助手全自动在后台处理，你只需要在聊天窗口用自然语言发指令即可。

### Q3：为什么对话时间长了浏览器会变卡？怎么解决？
**答**：
这不是网络或配置问题，而是因为单个对话积累了太多代码和历史记录，导致浏览器的页面渲染变重。
**最佳解决方案**：使用上面提到的**【旧对话生成交接摘要 ➔ 换新对话继续】**模式，一键生成摘要后新开对话，瞬间恢复极致流畅且上下文不丢失。

### Q4：支持哪些浏览器？
**答**：
- **Mac 用户**：可以继续使用 Safari bridge；也可以使用 BrowserSkill 驱动 **Google Chrome / Microsoft Edge / Brave 等 Chromium 浏览器**。
- 使用 BrowserSkill 时，可按 `instance_id` 或唯一 `label` 指定用户选定的浏览器 Profile，并在该 Profile 的独立 Agent Window 中运行。
- Windows 分支同样以 BrowserSkill / Chromium 为主要自动化方向。

### Q5：如果 AI 改出的代码不符合预期怎么办？
**答**：完全不用担心。
1. **随时打断调整**：你可以随时用自然语言说：“停一下，方案里的第 X 步我想改成……”，AI 会重新调整。
2. **一键安全回滚**：因为每一步都有 GitHub 自动存档，你只需说：“帮我撤回刚才的那次修改”，项目就能立刻恢复到改动前的完好状态。

### Q6：我的密码、密钥等隐私数据会泄露给 ChatGPT 吗？
**答**：**不会**。
系统内置了严格的敏感信息自动脱敏机制，在向 ChatGPT 传递上下文时，密码、Token、API Key 等敏感数据会被自动过滤掩码，确保代码安全无忧。

---

## 🎉 开始体验

现在就打开你的浏览器和 ChatGPT 对话，对你的桌面 AI 助手说出你的第一个想法吧！
