#!/bin/zsh

# ==============================================================================
# 成都建工 V3.0 财税智控与 IDP 穿透中枢 · 桌面一键停止终端
# ==============================================================================
set -u

export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"

PROJECT_DIR="/Users/yvoche/AI开发/073_成都建工/V3.0"
STOP_SCRIPT="${PROJECT_DIR}/stop_all.sh"
APP_PID_FILE="${PROJECT_DIR}/.app.pid"

wait_for_enter() {
  if [[ -t 0 ]]; then
    read -r "?按回车键关闭此窗口..." || true
  fi
}

print -- "============================================================"
print -- "  🛑 正在停止成都建工 V3.0 全部后台服务..."
print -- "============================================================"

if [[ -d "$PROJECT_DIR" && -f "$STOP_SCRIPT" ]]; then
  if [[ -x "$STOP_SCRIPT" ]]; then
    "$STOP_SCRIPT"
  else
    zsh "$STOP_SCRIPT"
  fi
fi

# 强制清理前端、IDP 与残留端口
for port in 8921 8922 5173 8930 8931 8933; do
  pids=$(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "$pids" | while read -r p; do
      [[ -n "$p" ]] && kill -9 "$p" 2>/dev/null || true
    done
  fi
done

# 辅以进程名特征清理残留孤儿进程
pkill -9 -f "uvicorn.*8921" 2>/dev/null || true
pkill -9 -f "uvicorn.*8922" 2>/dev/null || true
pkill -9 -f "uvicorn.*8933" 2>/dev/null || true
pkill -9 -f "llama-server.*8930" 2>/dev/null || true
pkill -9 -f "vite.*5173" 2>/dev/null || true

rm -f "${PROJECT_DIR}/.tax.pid" "${PROJECT_DIR}/.rag.pid" "${PROJECT_DIR}/.local_llm.pid" "${PROJECT_DIR}/.app.pid" "${PROJECT_DIR}/.idp.pid"

print -- ""
print -- "============================================================"
print -- "  ✅ 全部服务已成功停止，系统资源与端口已完全释放！"
print -- "============================================================"
print -- ""

wait_for_enter
exit 0
