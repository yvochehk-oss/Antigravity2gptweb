---
name: safari-chatgpt-reasoner
description: 双层混合 Agent 系统：以 Safari ChatGPT 网页端为云端认知控制面（High Intelligence / Zero Privilege），以 Antigravity 为本地确定性数据面（High Privilege / Low Ambiguity）。v4.0 起强制执行 Tab 精确绑定、基线比对与结构化退出码，确保证据完整性可机验。
---

# Safari ChatGPT Reasoner Skill (v4.1 Evidence-Integrity)

### 架构原则与契约 (Architecture Contracts)
1. **三权分立与单向依赖**：
   - **Reasoning Plane（ChatGPT Web）**：高阶架构推演、故障归因与规划（零系统读写权限）。
   - **Execution Plane（Antigravity）**：文件原子修改、依赖安装、环境构建（高权限确定性操作）。
   - **Verification Plane（Evidence Store）**：独立以 exit code、单测通过率、git diff 为客观事实准绳。
2. **Tab 精确绑定与自适应匹配 (P0-1)**：
   - 严禁模糊启发式，调用方必须通过 `--target-url` 指定目标 Tab。
   - 自动适配 Custom GPT URL 路径、标准 `/c/` 路径及会话 UUID，多 Tab 时智能优选已就绪的活动 Tab。
3. **基线比对与提交守卫 (P0-2)**：
   - 发送前记录 baseline 消息指纹（count + lastFp）；
   - 发送后强制验证用户消息已真实提交并产生新回合，杜绝 Enter 丢失却返回旧回复的伪证风险。
4. **结构化退出码与序列化边界脱敏 (P0-3 & P2)**：
   - `0`: 正常完成，证据完整
   - `2`: 超时（拿到部分内容）
   - `3`: 超时（无内容）
   - `4`: Safari / AppleScript 执行异常
   - `5`: 基线采集失败
   - `6`: 用户消息未真正提交
   - `7`: 助手新回合未产生
   - `10`: 目标 Tab 不存在 (`NO_TAB`)
   - `11`: 目标 Tab 歧义 (`AMBIGUOUS_TAB`)
   - `12`: 熔断器已开 (`CIRCUIT_OPEN`)
   - 所有事件走 stderr JSON（且递归脱敏凭据），stdout 保持干净纯文本。
5. **并发安全与错误特征规范化 (P1-B)**：
   - `_CircuitLock(fcntl.LOCK_EX)` 跨进程原子写；
   - 错误签名自动剔除 UUID、时间戳、临时路径、PID 等动态噪音，防范熔断漂移。

### 标准调用模式
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
  --evidence "$(cat /tmp/pytest_fail.log)" \
  --level L1 \
  --signature "ALEMBIC_MIGRATION_DUPLICATE_KEY_ERR"

# 3. 开辟全新干净会话
python3 ~/.gemini/config/skills/safari-chatgpt-reasoner/scripts/safari_chatgpt.py \
  --target-url "https://chatgpt.com/" \
  --new \
  --type plan \
  --prompt "启动新任务"
```
