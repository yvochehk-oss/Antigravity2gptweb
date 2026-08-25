#!/bin/zsh

# ==============================================================================
# 成都建工 V2.0 财税智控与 RAG 穿透系统 · 桌面一键停止终端
# ==============================================================================
set -u

export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"

PROJECT_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0"
STOP_SCRIPT="${PROJECT_DIR}/stop_all.sh"
APP_PID_FILE="${PROJECT_DIR}/.app.pid"

wait_for_enter() {
  if [[ -t 0 ]]; then
    read -r "?按回车键关闭此窗口..." || true
  fi
}

print -- "============================================================"
print -- "  🛑 正在停止成都建工 V2.0 全部后台服务..."
print -- "============================================================"

if [[ -d "$PROJECT_DIR" && -f "$STOP_SCRIPT" ]]; then
  if [[ -x "$STOP_SCRIPT" ]]; then
    "$STOP_SCRIPT"
  else
    zsh "$STOP_SCRIPT"
  fi
fi

# 强制清理前端与残留端口
for port in 8921 8922 5173 8930; do
  pids=$(lsof -t -i:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    kill -9 $pids 2>/dev/null || true
  fi
done

rm -f "${PROJECT_DIR}/.tax.pid" "${PROJECT_DIR}/.rag.pid" "${PROJECT_DIR}/.local_llm.pid" "${PROJECT_DIR}/.app.pid"

print -- ""
print -- "============================================================"
print -- "  ✅ 全部服务已成功停止，系统资源与端口已完全释放！"
print -- "============================================================"
print -- ""

wait_for_enter
exit 0
