---
name: antigravity-chatgpt-web-bridge
description: 一句话：让本地 Antigravity（Claude 3.7 Flash）快速出代码、把架构难题丢给 ChatGPT 网页版（GPT-5.6 SOL）做高强度推演、GitHub 留底每一次变更。复杂大任务从此可机验、可回滚、可协作。
---

# Antigravity × ChatGPT Web × GitHub —— 大任务开发的"三件套"

> **TL;DR**：你不用离开 IDE，就能让本地 Claude 3.7 Flash 帮你秒级写代码，把"接下来该干啥"的架构决策丢给 ChatGPT 网页版的 GPT-5.6 SOL 做高强度推演，每一步都有 GitHub 给你留证据。出错时 `git revert` 一秒回滚，事件 JSONL 帮你定位是第几步崩的。

---

## 这东西解决什么问题？

复杂任务（重构、迁移、新模块、性能调优）一般死在三个地方：

| 死法 | 原因 | 三件套怎么救 |
|---|---|---|
| 🪨 **推演跑偏** | 一个人闷头想架构，钻牛角尖 | 把架构问题丢给 ChatGPT 的 **GPT-5.6 SOL**（高强度模型）做规划 |
| 🐢 **落地慢** | 等 ChatGPT 写完才能动本地 | **Antigravity + Claude 3.7 Flash** 边推边改，秒级反馈 |
| 💥 **改错了找不到回滚点** | 改 30 个文件，最后崩了无法定位 | **GitHub** + **事件 JSONL** + **退出码** 三重证据，定位精确到 turn |

---

## 三个角色是怎么分工的？

```
        ┌─────────────────────────┐
        │   ChatGPT 网页版         │   ←  架构推演 / 故障归因 / 规划清单
        │   (GPT-5.6 SOL medium)  │       Zero Privilege：能给你方案
        └────────────▲────────────┘       不能动你的文件
                     │
                     │  stdout（纯文本回答）
                     │  stderr（事件 JSONL）
                     │
        ┌────────────┴────────────┐
        │   本地 Antigravity       │   ←  落地执行 / 改文件 / 跑测试
        │   (Claude 3.7 Flash)    │       High Privilege：能改你文件
        └────────────▲────────────┘       但不会自己想架构
                     │
                     │  git commit + push
                     │  Pull Request
                     │
        ┌────────────┴────────────┐
        │   GitHub                │   ←  证据留痕 / PR Review / 一键回滚
        └─────────────────────────┘
```

| 角色 | 干啥 | 不能干啥 |
|---|---|---|
| **ChatGPT 网页版 (GPT-5.6 SOL)** | 高强度架构推演、故障归因、给你"该做什么"的清单 | 改你的文件 |
| **Antigravity + Claude 3.7 Flash** | 快速写代码、改文件、跑测试、装依赖 | 想不清楚架构就闷头改 |
| **GitHub** | 留底每一次变更、做 PR Review、出问题时一键回滚 | — |

> **关键约束**：Antigravity 和 ChatGPT Web 之间是**单向依赖**——ChatGPT 不能"穿越"过来改你文件，只能给方案。Antigravity 必须主动把方案变成代码。这样出问题时责任清晰：**推演出错找 ChatGPT，落地出错找 Antigravity**。

---

## 5 分钟上手

### 第一步：装 Skill

```bash
git clone https://github.com/yvochehk-oss/Antigravity2gptweb.git \
  ~/.gemini/config/skills/antigravity-chatgpt-web-bridge
```

### 第二步：开 ChatGPT Tab

打开 Safari 或任何 Chromium 浏览器（Chrome / Edge / Brave 都行），到 https://chatgpt.com 开一个新对话，复制 URL：

```
https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0
```

记下来，待会儿用。

### 第三步：让 Antigravity 调用 ChatGPT 推演架构

```bash
python3 ~/.gemini/config/skills/antigravity-chatgpt-web-bridge/scripts/safari_chatgpt.py \
  --target-url "https://chatgpt.com/c/6a93f844-99f8-83ea-b4fd-8b544659e4a0" \
  --type plan \
  --prompt "我要重构我们项目的用户认证模块，当前用的是 Flask-Login + Session，请给我一个渐进式迁移到 FastAPI + JWT 的方案，分 5 步可独立 PR"
```

它会：
1. 把当前 git 状态 / 关键文件作为上下文喂给 ChatGPT
2. ChatGPT 用 GPT-5.6 SOL 给出 5 步迁移方案
3. 输出回答到 **stdout**（你直接读）
4. 把所有事件（基线、注入、提交、稳定阶段）写到 **stderr JSONL**（机读，可审计）
5. **退出码**告诉你成功 / 超时 / 浏览器挂掉

### 第四步：让 Antigravity + Claude 3.7 Flash 落地执行

ChatGPT 给完方案后，Antigravity 拿到方案，**直接动手**：
1. 改文件（Claude 3.7 Flash 出活快，每秒能改好几个文件）
2. 跑测试
3. 出错了 → 把报错日志喂回 ChatGPT 推演下一步（`--type feedback`）
4. 再改
5. 搞定 → `git commit` + `git push` → 开 PR

---

## 实战案例：30 个文件一夜迁移

> 场景：把老 Flask 项目迁移到 FastAPI。30 个文件、3 天工作量。
> 用本 skill 后：**Antigravity 一夜搞定，PR 上 ChatGPT 帮你 Review**。

### 阶段一：让 ChatGPT 给你方案（10 分钟）

```bash
# 第一次调用：推演整体方案
python3 scripts/safari_chatgpt.py \
  --target-url "$CHATGPT_URL" \
  --type plan \
  --prompt "把 Flask 项目迁移到 FastAPI。请给我一个分 5 步落地的计划，每步对应一个可独立测试、可独立 PR 的子任务。重点：会话兼容期怎么过渡。"
```

ChatGPT 会给你 5 步，每步对应一个可独立 PR。

### 阶段二：Antigravity + 3.7 Flash 动手改（一夜）

```bash
# 第二次调用：第一步执行反馈（带日志，让 ChatGPT 帮你 debug）
python3 scripts/safari_chatgpt.py \
  --target-url "$CHATGPT_URL" \
  --type feedback \
  --prompt "第一步：用户模型改造。下面是 alembic 迁移失败的日志，请帮我归因" \
  --evidence-file /tmp/alembic_error.log \
  --level L1 \
  --signature "ALEMBIC_MIGRATION_FAIL"
```

- `feedback` 类型 = 把本地证据（错误日志）喂给 ChatGPT，让它帮你归因
- `--evidence-file` = 大日志走文件，避免命令行参数过长
- `--signature` = 给这次失败打个标签，下次再出同样的错就熔断，不再骚扰 ChatGPT

每改完一文件，git commit 一次。出问题立刻 `git revert` 回滚。

### 阶段三：ChatGPT 帮你 Review PR（10 分钟）

```bash
python3 scripts/safari_chatgpt.py \
  --target-url "$CHATGPT_URL" \
  --type review \
  --prompt "这是迁移第一步的 PR: https://github.com/xxx/pull/123，请帮我 Review，重点看 JWT 鉴权是否漏了边界条件、过期处理是否正确"
```

### 阶段四：合并 + 下一轮

PR merge → 切回阶段二，跑第二步。循环直到 5 步全做完。

---

## 浏览器怎么选？

| 你的环境 | 推荐 | 备注 |
|---|---|---|
| **macOS 主力开发** | Safari | AppleScript 直接驱动，零配置 |
| **Linux / Windows** | Chrome / Edge / Brave | 启动时加 `--remote-debugging-port=9222` |
| **公司电脑只有特定浏览器** | 任何 Chromium 内核 | Edge / Brave / Arc / Opera 都通用，只换启动命令 |

> ⚠️ **不要同时在 9222 端口启两个 Chromium 实例**（比如 Chrome 和 Edge 都开着）。端口冲突会让 `/json/list` 返回错乱 Tab。建议 Edge 用户把端口改成 9223，调用时加 `--chrome-port 9223`。

---

## 退出码速查（看到数字不用慌）

| 退出码 | 含义 | 你该怎么办 |
|---|---|---|
| **0** | 完美 | 读 stdout |
| **2** | 超时但拿到部分内容 | 读 stdout（可能不全） |
| **3** | 超时无内容 | 换浏览器 / 重连 ChatGPT |
| **4** | 浏览器或 JS 挂了 | 看 stderr 事件 JSONL 定位阶段 |
| **6** | 用户消息没真提交 | ChatGPT 在刷新？重试 |
| **10** | 找不到目标 Tab | 检查 `--target-url` 是不是当前 Tab |
| **11** | 多个 Tab 匹配 | 关掉其它 ChatGPT Tab |
| **12** | 熔断器开了 | 同签名 1 小时内失败 3 次。等 1 小时，或 `--reset-circuit` |

---

## 常见问题

### Q：调用 ChatGPT 会不会很慢？
A：ChatGPT 网页版回复约 20-60 秒，本地 Antigravity 等就行，闲时可以并行跑别的任务。

### Q：失败重试机制是怎样的？
A：只对**只读操作**（基线、查询）自动重试；**写操作**（inject / send）fail-fast——宁可让你手动重试，也不要它自作主张重发。

### Q：熔断器是干啥的？
A：防止死循环。同样的报错 1 小时内出现 3 次，自动开熔断，避免你被 ChatGPT 反复打脸。

### Q：Antigravity 的本地模型能换成别的吗？
A：可以。本 skill 只规定 Antigravity 怎么"调用 ChatGPT Web"，不约束 Antigravity 自己跑哪个模型。Claude 3.7 Flash 是快，3.7 Sonnet 更准，自己挑。

### Q：能跑在 GitHub Actions / CI 里吗？
A：可以，但必须用 Chromium 版（带 `--remote-debugging-port`）启动无头浏览器。Safari 版只能在 macOS 跑。

### Q：调用会泄露我的代码 / 密钥给 ChatGPT 吗？
A：所有事件 JSONL 在序列化前都做**递归脱敏**（GitHub PAT、OpenAI Key、AWS Key、JWT、数据库连接串、`.pem` 私钥等）。日志里看到的是 `***REDACTED***`，原始值绝不出库。

---

## 下一步

- 想了解每个退出码的精确定义？看 [SKILL.md](SKILL.md)
- 想看完整调用范例？看 [SKILL.md](SKILL.md) 第 "标准调用模式" 一节
- 想加自定义浏览器 / 自定义 sanitizer？PR 欢迎！