# Safari ChatGPT Bridge v4.0 — 真实回归套件

> **前提**：Safari 已登录 ChatGPT，对应 Tab 已打开 URL，对话会话 ID 已知。
> 本目录的脚本会在真实 Safari 中触发 ChatGPT 回复，**会消耗 token 并留下真实历史**。

---

## 文件清单

| 文件 | 用途 |
|---|---|
| `v4_regression.sh` | 主回归脚本，一次跑完 plan_ok / feedback_ok / no_tab |
| `assert_exit.py` | 解析 stderr JSONL 事件流并断言退出码 + 阶段顺序 |
| `events_*.jsonl` | 每次回归生成的 stderr 事件流（保留用于审计） |
| `answer_*.txt` | 每次回归的 stdout 回答文本 |
| `result_*.txt` | 每次回归的 assert_exit.py 输出 |
| `passed_tests.txt` / `failed_tests.txt` | 每次运行末尾汇总 |

---

## 使用方法

### 1. 准备 Safari

- Safari 登录 `chatgpt.com`
- 打开**目标 ChatGPT 会话**（任何稳定会话即可）
- 复制地址栏完整 URL，例如 `https://chatgpt.com/c/abc12345-...`
- macOS 第一次运行会被系统询问"允许 osascript 控制 Safari"，请点同意

### 2. 执行

```bash
cd /Users/yvoche/AI开发/073_成都建工/V3.0/source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/scripts/v4_regression

# 方法一：环境变量
export TARGET_URL="https://chatgpt.com/c/你的会话ID"
bash v4_regression.sh

# 方法二：交互输入
bash v4_regression.sh
# → 提示时粘贴 URL
```

### 3. 解读结果

- ✅ `passed_tests.txt` 包含所有通过的回归项
- ❌ `failed_tests.txt` 包含失败的回归项
- 每个 `events_*.jsonl` 可独立用 `python3 assert_exit.py <expected_exit> events_*.jsonl --expect-stages ...` 重新断言

---

## 三个回归项的覆盖目标

| 回归项 | 触发 | 期望退出码 | 期望阶段 |
|---|---|---|---|
| `plan_ok` | plan 协议，问极简问题（<30 字），ChatGPT 应秒答 | `0` | start → baseline → inject → send → submit_verify → new_turn → stable → done |
| `feedback_ok` | feedback 协议，附带工程错误信息 | `0` | 同上 |
| `no_tab` | 故意使用错误的 URL | `10` | start → baseline（失败并退出） |

---

## 断言机制（assert_exit.py）

`assert_exit.py` 验证五件事：

1. **第一条事件是 `start`**
2. **最后一条事件的 `exit_code` 等于主进程返回的退出码**
3. **退出码对应的语义名（`exit_name`）与退出码本身一致**
4. **阶段顺序**（可选，通过 `--expect-stages` 指定）
5. **每个事件必含字段**：`ts / stage / exit_code / exit_name / message`

任何一条不满足都判定 FAIL，并打印具体差异。

---

## 常见失败诊断

### 失败：no_tab 退出了 5 而非 10
- **诊断**：说明 `NoTargetTabError` 未透传（应已在 v4.0 修复）
- **检查**：`grep -n "except NoTargetTabError" safari_chatgpt.py | wc -l` 应 >= 8

### 失败：plan_ok 超时（exit=2 或 3）
- **诊断**：ChatGPT 思考超过 180s（罕见但可能）
- **缓解**：加大 `--timeout`，或拆分 prompt 让它更快响应

### 失败：submit_verify 超时（exit=6）
- **诊断**：用户消息没真正提交。可能原因：Safari 中无 prompt-textarea 元素（DOM 改版）
- **检查**：手动在 Safari 看一下输入框是否有变化

### 失败：new_turn 超时（exit=7）
- **诊断**：ChatGPT 没产生新回复。可能原因：模型被中断、网络问题
- **检查**：手动查看 Safari 该 Tab 是否真的有响应

---

## 风险与注意事项

- **每次运行会真实发起 2-3 条 ChatGPT 提问**，消耗 token 与对话历史
- **熔断器状态文件** `/tmp/safari_chatgpt_circuit_breaker.json` 在每次运行前会清空
- **不要在重要对话中运行**，避免污染。建议先新建一个临时会话

---

## P3（待办）

P3 的 `--poll-only` 模式与流式输出会在本回归通过后再接入。具体设计见 SKILL.md 路线图。