#!/usr/bin/env bash
# =============================================================================
# v4_regression.sh — Safari Bridge v4.0 端到端回归套件
# =============================================================================
# 使用方法：
#   # 方法一：环境变量
#   export TARGET_URL="https://chatgpt.com/c/你的会话ID"
#   bash v4_regression.sh
#
#   # 方法二：交互输入
#   bash v4_regression.sh
#   （提示时粘贴 Safari ChatGPT Tab 的 URL）
#
# 前置条件：
#   1. Safari 已登录 ChatGPT (chatgpt.com)
#   2. Safari 已开启"开发菜单 → 允许 JavaScript from Apple Events"
#      （系统设置 → 隐私与安全性 → Apple Events → Safari → 允许）
#   3. Safari 已打开 TARGET_URL 对应的 Tab
#
# 回归项：
#   plan_regression   — plan 协议 + 正常完成（exit=0）
#   feedback_regression — feedback 协议 + 正常完成（exit=0）
#   no_tab_regression  — 故意写错 URL → exit=10
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_SCRIPT="$HOME/.gemini/config/skills/safari-chatgpt-reasoner/scripts/safari_chatgpt.py"
ASSERT_SCRIPT="${SCRIPT_DIR}/assert_exit.py"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CIRCUIT_FILE="/tmp/safari_chatgpt_circuit_breaker.json"

# ---------------------------------------------------------------------------
# 颜色输出
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[INFO]${RESET} $*"; }
pass() { echo -e "${GREEN}✅ PASS${RESET} $*"; }
fail() { echo -e "${RED}❌ FAIL${RESET} $*" >&2; }
warn() { echo -e "${YELLOW}⚠️  WARN${RESET} $*"; }

# ---------------------------------------------------------------------------
# 获取 TARGET_URL
# ---------------------------------------------------------------------------
get_target_url() {
    if [[ -n "${TARGET_URL:-}" ]]; then
        log "使用环境变量 TARGET_URL: ${TARGET_URL:0:50}..."
        return 0
    fi
    echo ""
    echo -e "${BOLD}请粘贴 Safari ChatGPT Tab 的完整 URL：${RESET}"
    echo -e "  提示：在 Safari 打开 ChatGPT 会话，复制地址栏内容"
    echo -n "  TARGET_URL: "
    read -r TARGET_URL
    if [[ -z "$TARGET_URL" ]]; then
        fail "TARGET_URL 为空，退出"
        exit 1
    fi
    export TARGET_URL
    log "已设置 TARGET_URL: ${TARGET_URL:0:50}..."
}

# ---------------------------------------------------------------------------
# 清理前置状态
# ---------------------------------------------------------------------------
cleanup() {
    log "清理前置状态（熔断器状态文件）..."
    rm -f "$CIRCUIT_FILE"
    rm -f "${CIRCUIT_FILE}.lock"
}

# ---------------------------------------------------------------------------
# 执行单次回归
# ---------------------------------------------------------------------------
run_regression() {
    local name="$1"; shift
    local prompt_arg="$1"; shift
    local expected_exit="$1"; shift
    local expected_stages="${1:-}"; shift

    local out_file="${SCRIPT_DIR}/events_${name}.jsonl"
    local answer_file="${SCRIPT_DIR}/answer_${name}.txt"
    local result_file="${SCRIPT_DIR}/result_${name}.txt"

    log "━━━ 开始回归: ${name} ━━━"
    log "  prompt: ${prompt_arg:0:60}..."
    log "  期望退出码: ${expected_exit} ($(python3 "${ASSERT_SCRIPT}" 2>/dev/null | \
        grep "^EXIT_CODE_NAME = {" | \
        grep -oP "'${expected_exit}': '\w+'" || echo "${expected_exit}"))"
    log "  输出文件: ${out_file}"

    # 清空旧输出
    > "$out_file"
    > "$answer_file"
    > "$result_file"

    local actual_exit=0
    local start_time=$(python3 -c 'import time; print(time.time())')

    # 执行桥接器：stdout → answer.txt, stderr → events.jsonl
    "$BRIDGE_SCRIPT" \
        --target-url "$TARGET_URL" \
        --prompt "$prompt_arg" \
        --timeout 180 \
        --cwd "$PROJECT_ROOT" \
        "$@" \
        > "$answer_file" \
        2>> "$out_file"
    actual_exit=$?

    local elapsed=$(python3 -c "import time; print(f'{time.time() - ${start_time}:.1f}')")

    # 打印事件流（彩色）
    echo -e "\n  ${BOLD}事件流：${RESET}"
    while IFS= read -r line; do
        if [[ -z "$line" ]]; then continue; fi
        ec=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('exit_code',''))" 2>/dev/null)
        stage=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('stage',''))" 2>/dev/null)
        msg=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message','')[:50])" 2>/dev/null)
        if [[ "$ec" == "0" ]]; then
            echo -e "    ${GREEN}[$ec]${RESET} $stage — $msg"
        else
            echo -e "    ${RED}[$ec]${RESET} $stage — $msg"
        fi
    done < "$out_file"

    # 打印回答摘要
    local answer_len=$(wc -c < "$answer_file")
    echo -e "\n  ${BOLD}回答摘要（${answer_len} 字节）：${RESET}"
    if [[ -s "$answer_file" ]]; then
        head -c 200 "$answer_file" | sed 's/^/    /'
        if [[ "$answer_len" -gt 200 ]]; then
            echo -e "    ${YELLOW}...（省略 $((answer_len - 200)) 字节）${RESET}"
        fi
    else
        echo -e "    ${YELLOW}(无回答文本)${RESET}"
    fi

    # 断言验证
    echo ""
    local assert_cmd=(
        python3 "$ASSERT_SCRIPT"
        "$expected_exit"
        "$out_file"
    )
    if [[ -n "$expected_stages" ]]; then
        assert_cmd+=("--expect-stages" "$expected_stages")
    fi

    local assert_exit_code=0
    "${assert_cmd[@]}" >> "$result_file" 2>&1 || assert_exit_code=$?

    cat "$result_file"

    if [[ "$assert_exit_code" -eq 0 ]]; then
        pass "$name 回归通过（exit=${actual_exit}, elapsed=${elapsed}s）"
        echo "$name" >> "${SCRIPT_DIR}/passed_tests.txt"
        return 0
    else
        fail "$name 回归失败（exit=${actual_exit}, expected=${expected_exit}）"
        echo "$name" >> "${SCRIPT_DIR}/failed_tests.txt"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# 主体
# ---------------------------------------------------------------------------
main() {
    echo -e "${BOLD}"
    echo "═══════════════════════════════════════════════════════════"
    echo "  Safari ChatGPT Bridge v4.0 端到端回归"
    echo "═══════════════════════════════════════════════════════════"
    echo -e "${RESET}"

    # 检查依赖
    if [[ ! -f "$BRIDGE_SCRIPT" ]]; then
        fail "找不到桥接器脚本: $BRIDGE_SCRIPT"
        exit 1
    fi
    if [[ ! -f "$ASSERT_SCRIPT" ]]; then
        fail "找不到断言脚本: $ASSERT_SCRIPT"
        exit 1
    fi
    if ! command -v osascript &>/dev/null; then
        fail "osascript 不可用（仅 macOS）"
        exit 1
    fi

    # 清理旧状态
    cleanup

    # 获取 URL
    get_target_url

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}  开始回归（共 3 项）${RESET}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"

    > "${SCRIPT_DIR}/passed_tests.txt"
    > "${SCRIPT_DIR}/failed_tests.txt"

    local total_failed=0

    # ---- 回归 1：plan 正常完成 ----
    if ! run_regression \
        "plan_ok" \
        "请用一句话说明 HTTPS 的工作原理，不要超过 30 字。" \
        0 \
        "start,baseline,inject,send,submit_verify,new_turn,stable,done"; then
        ((total_failed++))
    fi
    echo ""

    # ---- 回归 2：feedback 正常完成 ----
    if ! run_regression \
        "feedback_ok" \
        "我正在开发一个税务管理系统，其中增值税计算逻辑遇到如下错误：
TypeError: unsupported operand type(s) for -: 'NoneType' and 'Decimal'
请分析可能的原因并给出修复建议。" \
        0 \
        "start,baseline,inject,send,submit_verify,new_turn,stable,done"; then
        ((total_failed++))
    fi
    echo ""

    # ---- 回归 3：错误路径：Tab 不存在 ----
    if ! run_regression \
        "no_tab" \
        "这条消息不会被发送" \
        10 \
        "start,baseline"; then
        ((total_failed++))
    fi
    echo ""

    # ---------------------------------------------------------------------------
    # 汇总
    # ---------------------------------------------------------------------------
    echo -e "${BOLD}═══════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}  回归汇总${RESET}"

    local passed_count=0 failed_count=0
    if [[ -f "${SCRIPT_DIR}/passed_tests.txt" ]]; then
        passed_count=$(wc -l < "${SCRIPT_DIR}/passed_tests.txt")
    fi
    if [[ -f "${SCRIPT_DIR}/failed_tests.txt" ]]; then
        failed_count=$(wc -l < "${SCRIPT_DIR}/failed_tests.txt")
    fi

    echo -e "  通过: ${GREEN}${passed_count}${RESET}"
    echo -e "  失败: ${RED}${failed_count}${RESET}"

    echo ""
    echo -e "${BOLD}事件文件位置：${RESET}"
    for name in plan_ok feedback_ok no_tab; do
        echo "  ${SCRIPT_DIR}/events_${name}.jsonl"
    done

    if [[ "$failed_count" -gt 0 ]]; then
        echo ""
        fail "有 ${failed_count} 项回归失败，请检查上述事件文件"
        exit 1
    else
        echo ""
        pass "全部 ${passed_count} 项回归通过 ✅"
        exit 0
    fi
}

main "$@"
