# 成都建工 V2.0 Agent 开发与分支同步规则 (AGENTS.md)

本文件规定了 AI Agent 在本项目中进行代码修改、Git 同步、版本提交和跨平台治理的核心原则与行为准则。

---

## 1. 单一主体代码与分支镜像架构 (Canonical Branch Mirror)

* **唯一代码基线**：`main` 是项目的唯一主体基线。`windows` 和 `macos` 是对同一代码基线的镜像引用。三者正常情况下必须始终指向**同一个 Commit SHA**。
* **业务源码零分叉**：所有子系统（`0.1_税务管理`、`0.2_RAG系统`、`0.3_老板端安卓App_天府掌舵`、前端 UI、数据库模型与迁移）在 Windows 与 macOS 上共用**同一套共享源码**。
* **平台差异严格收敛在启动脚本**：
  * macOS / Linux：`start_all.sh`、`stop_all.sh` 等 Shell 脚本；
  * Windows：`00_一键启动` ~ `99_停止全部`、`START_WINDOWS.bat`、`windows_scripts/` 等批处理脚本。
  * 严禁在 `source_code/` 内部为不同操作系统建立分叉目录或分支特异性代码！

---

## 2. Agent 提交与推送原则

1. **工作分支**：
   * 在 Windows 机器上开发时，本地处于 `windows` 分支；
   * 提交代码必须使用标准清晰的 Conventional Commits 格式（如 `feat(tax): ...`、`fix(rag): ...`、`docs(sync): ...`）。
2. **快速线性提交与自动镜像**：
   * 本地提交后直接推送到远程：`git push origin windows`；
   * 远程 GitHub Actions（`.github/workflows/cross-platform-sync.yml`）会自动触发 **Canonical Branch Mirror**，在校验通过后以 fast-forward 方式将 `main` 和 `macos` 原子同步至同一 Commit SHA。
3. **安全同步命令**：
   * 本地拉取远程最新代码时，必须使用 **`--ff-only`** 线性快进同步（或运行 `windows_scripts/sync_from_macos.bat`），避免生成无意义的 merge commit。

---

## 3. 严格禁止的破坏性操作 (Prohibited Actions)

Agent 在任何情况下**绝对禁止**执行以下操作：
❌ **禁止** 使用 `git checkout --theirs source_code/` 或 `git checkout --ours source_code/` 盲目覆盖业务代码。
❌ **禁止** 在发生冲突时未经业务代码逐行审查直接 `git add -A` 并无脑 commit。
❌ **禁止** 使用 `--allow-unrelated-histories` 强行将不同历史的分支拼接到一起。
❌ **禁止** 将虚拟环境（`.venv`、`.mineru-venv`）、GGUF 大模型权重（>100MB）、Postgres 数据库二进制目录（`database/`）或超大备份文件（`*.dump`）提交到 Git。

---

## 4. 分叉与冲突应急处理机制

如果由于双端同时开发导致远程分支分叉，CI 自动镜像将自动熔断并停止同步。
此时的处理流程为：
1. 检出 `main` 分支，人工审查并精准整合双端的有效业务修改；
2. 运行系统全量回归验证与测试；
3. 将统一后的提交同步到 `main`，并依次将 `windows` 和 `macos` 以 `--ff-only` 恢复为相同 SHA。

---

## 5. 双层混合 Agent 架构协同与本地 Agent“零自主写代码”铁律 (Dual-Agent Supreme Law)

1. **认知控制面与执行数据面严格隔离**：
   - **云端认知与代码控制面（Safari / Chrome Custom GPT）**：配备 GitHub 直连读写工具，拥有独占的系统架构推演、故障归因、实施方案制定以及远程仓库代码修改与提交推送（commits / PRs）权。
   - **本地执行与验收数据面（本地 Agent）**：职责严格限定为**监视 GPT 进度、督促其工作、`git pull` 拉取远端代码、在本地真实环境中运行 GPT 指定的验证命令，并收集客观测试证据反馈给 GPT**。

2. **本地 Agent 绝对禁止越俎代庖写业务代码（“你不要改程序”）**：
   - **核心禁令**：本地 Agent 绝对禁止自主编写、改写任何业务代码、配置文件或执行私自重构；
   - **闭环流向**：所有代码变更必须先由 Custom GPT 在 GitHub 远端仓库实施并推送，本地 Agent 仅通过 `git pull` 同步拉取；
   - **防抢跑纪律**：当 Custom GPT 处于深度思考（Reasoning）、工具调用（GitHub Actions / API）或流式生成时，本地 Agent 必须保持耐心监视，绝对严禁以“等待时间较长”为由擅自编写或修改本地代码。

3. **单步流式推进与闭环核准契约**：
   - Custom GPT 在远端推送代码后，必须显式给出可验证的本地测试命令（`TEST: <实际命令>` 与 `EXPECTED: <预期断言与退出码>`）；
   - 本地 Agent 执行 `git pull` 后完整运行该检验命令，收集标准输出、错误日志与退出码，通过 bridge 回传给 GPT 审查（`task-review`）；
   - 必须获得 Custom GPT 审查后显式裁决 `APPROVED`，该任务方可标记完成或推进至下一步。

4. **/safari-chatgpt-reasoner Skill 执行核心行为守则 (Local Agent Behavioral Protocol)**：
   当在项目中调用或启用了 `/safari-chatgpt-reasoner` 这一 Skill 时，本地 Agent 的唯一法定职责就是**全流程监督 GPT 推进工作**，严格恪守以下五大执行准则：
   - **① 监督 GPT 干活（禁止越权写代码）**：本地 Agent 绝对禁止自己编写、改写任何业务代码或擅自实施重构，所有架构推演、代码改写与提交推送由 Custom GPT 在 GitHub 远端全权执行；
   - **② 定时检查进度**：本地 Agent 必须通过定时器（如 schedule）主动定期检查 GPT 的状态与进度，观察其是处于深度思考、工具调用还是流式输出；
   - **③ 有问必答（响应阻断）**：若 GPT 在对话中提出任何疑问、方案确认、上下文索取或决策请求，本地 Agent 必须第一时间准确答复，不得拖延阻断；
   - **④ 停顿即催促（防假死）**：若 GPT 在未完成目标任务时出现非预期的停顿、中断、超时或假死，本地 Agent 必须立即重新发送指令，明确督促其继续推进当前任务；
   - **⑤ 本地执行验证（闭环跑测）**：当 GPT 在 GitHub 完成步骤并发出本地测试命令（`TEST:` / `EXPECTED:`）后，本地 Agent 立即执行 `git pull` 同步最新代码，在真实本地环境中严格执行该检验命令，将客观日志与退出码如实回传给 GPT 进行审查裁决（`task-review`）。

---

## 6. 桌面端控制台工程、状态栏 Logo 与 SSOT 规范 (Desktop Apps & Status Bar Logo Specification)

1. **桌面工程 GitHub 统一纳入与 SSOT 契约**：
   - 全系统桌面端控制台源码与资源（macOS: `desktop_apps/macos`，Windows: `desktop_apps/windows`）必须完整纳入 GitHub 远程仓库分支统一管理；严禁脱离远程仓库形成本地孤岛。
   - 二进制编译产物（如 `desktop_apps/macos/build/`、`成都建工控制台.app`、`bin/`、`obj/`）严禁提交至 Git，必须在 `.gitignore` 中彻底排除。

2. **macOS 状态栏（Menu Bar / NSStatusItem）品牌 Logo 架构准则 (ADR-MAC-MENUBAR-ICON-01)**：
   - **官方品牌彩色 Logo**：采用官方 192×192 PNG 作为母版，自包含存放于 `desktop_apps/macos/Resources/StatusLogo.png`，严禁跨子系统动态软链接；
   - **严禁 Template 化（`image.isTemplate = false`）**：官方橙底黑字“建”Logo 为不透明实体资产，禁止设置为 Template Image，以防被 macOS 渲染成纯黑/纯白无辨识度色块；该橙黑配色在深色与浅色菜单栏中均具有极高对比度与品牌清晰度；
   - **Retina 逻辑尺寸**：设置 `image.size = NSSize(width: 18, height: 18)`，由 AppKit backing scale 自动渲染 2x Retina 像素（36×36 px），保持硬边清晰；
   - **品牌身份与运行时状态解耦**：
     - **Logo（产品身份）**：常驻显示，生命周期仅在应用启动时配置加载一次；
     - **运行时状态（Runtime State）**：通过独立的动态彩色圆点（`●`）呈现（正常为绿、启动中为蓝、降级为橙、异常为红、停止为灰）；
     - **状态文字**：显式指定为 `NSColor.labelColor`，确保深色/浅色模式文字自适应可读；状态变化时仅刷新圆点颜色、标题与 ToolTip，严禁重刷或改变品牌 Logo。

3. **构建脚本 Fail-Closed 与运行时优雅降级**：
   - 构建脚本 `build_app.sh` 打包前必须执行 `StatusLogo.png` 存在性校验，若源资源缺失则构建立刻中断报错（Fail-Closed），严禁产出缺图的残缺 App；
   - 运行时若发生极端 Bundle 资源缺失，必须具备优雅降级机制（降级为系统 SF Symbol `building.2.crop.circle`）。

