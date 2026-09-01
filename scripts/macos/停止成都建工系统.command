#!/bin/zsh

# ==============================================================================
# 成都建工 V3.0 财税智控与 IDP 穿透中枢 · 桌面一键停止终端
# ==============================================================================
set -u

# 1. 注入标准环境路径
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:${HOME}/.local/bin:${HOME}/.cargo/bin:${HOME}/Library/Python/3.12/bin:${PATH}"

# 2. 动态解析工程根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${CHENGDU_PROJECT_DIR:-${SCRIPT_DIR}}"

STOP_SCRIPT="${PROJECT_DIR}/stop_all.sh"
APP_PID_FILE="${PROJECT_DIR}/.app.pid"
ROOT_ENV_FILE="${PROJECT_DIR}/.env"
RUNTIME_ENV_FILE="${PROJECT_DIR}/.chengdu.env"

# 加载可选配置覆盖
for env_f in "$ROOT_ENV_FILE" "$RUNTIME_ENV_FILE"; do
  if [[ -f "$env_f" ]]; then
    set -a
    source "$env_f" 2>/dev/null || true
    set +a
  fi
done

wait_for_enter() {
  if [[ -t 0 ]]; then
    print -P "\n%F{242}按回车键退出此窗口...%f"
    read -r "? " || true
  fi
}

print -P "%F{196}==================================================================%f"
print -P "%F{196}  🛑 正在停止成都建工 V3.0 全部后台服务...%f"
print -P "%F{196}==================================================================%f"

# 3. 执行核心停止脚本
if [[ -d "$PROJECT_DIR" && -f "$STOP_SCRIPT" ]]; then
  print -P "%F{220}⏳ [1/3] 正在通知后端核心集群优雅停止...%f"
  if [[ -x "$STOP_SCRIPT" ]]; then
    "$STOP_SCRIPT"
  else
    zsh "$STOP_SCRIPT"
  fi
fi

# 4. 停止老板端前端及残留 Vite 进程
print -P "%F{220}⏳ [2/3] 正在停止老板端前端与残留服务...%f"
if [[ -f "$APP_PID_FILE" ]]; then
  app_pid=$(cat "$APP_PID_FILE" 2>/dev/null || true)
  if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null; then
    kill -TERM "$app_pid" 2>/dev/null || true
    sleep 0.5
    kill -9 "$app_pid" 2>/dev/null || true
  fi
fi

# 5. 严格释放业务端口（8921, 8922, 8933, 8930, 8931, 5173, 3000）
for port in 8921 8922 8933 8930 8931 5173 3000; do
  pids=$(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "$pids" | while read -r p; do
      [[ -n "$p" ]] && kill -9 "$p" 2>/dev/null || true
    done
  fi
done

# 辅以特征清理残留进程
pkill -9 -f "uvicorn.*8921" 2>/dev/null || true
pkill -9 -f "uvicorn.*8922" 2>/dev/null || true
pkill -9 -f "uvicorn.*8933" 2>/dev/null || true
pkill -9 -f "llama-server.*8930" 2>/dev/null || true
pkill -9 -f "vite.*5173" 2>/dev/null || true
pkill -9 -f "vite.*3000" 2>/dev/null || true

# 清理 PID 记录文件
rm -f "${PROJECT_DIR}/.tax.pid" "${PROJECT_DIR}/.rag.pid" "${PROJECT_DIR}/.local_llm.pid" "${PROJECT_DIR}/.app.pid" "${PROJECT_DIR}/.idp.pid"

# 6. 验证端口释放状态
print -P "%F{220}🔍 [3/3] 正在验证端口释放状态...%f"
all_clear=true
for port in 8921 8922 8933 8930 5173; do
  if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
    print -P "  %F{196}⚠️ 端口 ${port} 仍有占用，请人工检查。%f"
    all_clear=false
  fi
done

print -- ""
if [[ "$all_clear" == "true" ]]; then
  print -P "%F{46}==================================================================%f"
  print -P "%F{46}  ✅ 全部业务服务已成功停止，端口与内存资源已完全释放！%f"
  print -P "%F{46}==================================================================%f"
else
  print -P "%F{220}==================================================================%f"
  print -P "%F{220}  ⚠️ 服务已停止，但存在部分残留端口监听。%f"
  print -P "%F{220}==================================================================%f"
fi
print -P "  🗄️ 注：PostgreSQL (5432) 数据库服务保持运行，保障数据安全与外部复用。"
print -- ""

wait_for_enter
exit 0

