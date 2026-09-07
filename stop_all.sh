#!/usr/bin/env bash
# 成都建工 V3.0 优雅、幂等停止脚本。
# 只处理由 V3.0 PID 文件确认的 IDP/Tax/RAG/LLM 进程，不按端口盲杀其他程序。

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_ENV_FILE="$PROJECT_DIR/.env"
TAX_PID_FILE="$PROJECT_DIR/.tax.pid"
RAG_PID_FILE="$PROJECT_DIR/.rag.pid"
IDP_PID_FILE="$PROJECT_DIR/.idp.pid"
LOCAL_LLM_PID_FILE="$PROJECT_DIR/.local_llm.pid"
_LOCAL_LLM_PORT_WAS_SET="${LOCAL_LLM_PORT+x}"
_LOCAL_LLM_PORT_OVERRIDE="${LOCAL_LLM_PORT-}"
_IDP_PORT_WAS_SET="${IDP_PORT+x}"
_IDP_PORT_OVERRIDE="${IDP_PORT-}"
if [ -f "$ROOT_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_ENV_FILE"
  set +a
fi
if [ "$_LOCAL_LLM_PORT_WAS_SET" = x ]; then LOCAL_LLM_PORT="$_LOCAL_LLM_PORT_OVERRIDE"; fi
if [ "$_IDP_PORT_WAS_SET" = x ]; then IDP_PORT="$_IDP_PORT_OVERRIDE"; fi
TAX_PORT="${TAX_PORT:-8921}"
RAG_PORT="${PROJECT_RAG_PORT:-8922}"
IDP_PORT="${IDP_PORT:-8933}"
LOCAL_LLM_PORT="${LOCAL_LLM_PORT:-8930}"
STOP_TIMEOUT_SECONDS="${STOP_TIMEOUT_SECONDS:-20}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[stop_all]${NC} $1"; }
warn() { echo -e "${YELLOW}[stop_all]${NC} WARNING: $1" >&2; }

is_numeric_pid() {
  case "${1:-}" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

pid_alive() {
  is_numeric_pid "${1:-}" || return 1
  # 主路径：使用 POSIX 标准的 kill -0 进行活跃度探测。
  if kill -0 "$1" >/dev/null 2>&1; then return 0; fi
  # 回退路径：部分受限环境（容器/沙箱/受限 macOS 启动代理）会拒绝
  # 对其他用户进程的 kill 调用，但 lsof 仍可读取 socket 关联；若
  # 该 PID 至少持有一个 LISTEN 套接字即视为存活。仍无法判定则视为已死。
  if command -v lsof >/dev/null 2>&1 \
     && lsof -nP -a -p "$1" -iTCP 2>/dev/null | grep -q LISTEN; then
    return 0
  fi
  return 1
}

pid_command() {
  ps -p "$1" -o command= 2>/dev/null || :
}

expected_service_process() {
  local pid="$1" port="$2" command
  command="$(pid_command "$pid")"
  case "$command" in
    *uvicorn*"--port $port"*|*uvicorn*"--port"$'\t'"$port"*) return 0 ;;
    *) return 1 ;;
  esac
}

expected_local_llm_process() {
  local pid="$1" port="$2" command
  command="$(pid_command "$pid")"
  case "$command" in
    *llama-server*"--port $port"*|*llama-server*"--port"$'\t'"$port"*) return 0 ;;
    *) return 1 ;;
  esac
}

# 取 PID 的当前工作目录（cwd）。使用 lsof 以保证 macOS 兼容。
pid_cwd() {
  local pid="$1"
  is_numeric_pid "$pid" || return 1
  lsof -a -p "$pid" -d cwd -F n 2>/dev/null \
    | awk '/^n/ {sub(/^n/, ""); print; exit}'
}

# 综合判断：进程既可能匹配"已知的命令行模板"，也可能其 cwd 在本项目根下。
# 当 PID 文件丢失但端口仍被 V3.0 项目目录下的进程占住时，按 cwd 兜底，避免
# 重启时出现"控制入口执行失败，退出码：1"。仅在 lsof 可用时启用 cwd 回退。
pid_owned_by_project() {
  local pid="$1" port="$2" kind="$3"
  case "$kind" in
    llm)
      if expected_local_llm_process "$pid" "$port"; then return 0; fi
      ;;
    *)
      if expected_service_process "$pid" "$port"; then return 0; fi
      ;;
  esac

  if ! command -v lsof >/dev/null 2>&1; then
    return 1
  fi
  local cwd
  cwd="$(pid_cwd "$pid")" || return 1
  case "$cwd" in
    "$PROJECT_DIR"/*) return 0 ;;
    *) return 1 ;;
  esac
}

wait_until_stopped() {
  local pid="$1" deadline=$((SECONDS + STOP_TIMEOUT_SECONDS))
  while pid_alive "$pid" && [ "$SECONDS" -lt "$deadline" ]; do
    sleep 1
  done
  ! pid_alive "$pid"
}

stop_service() {
  local name="$1" pid_file="$2" port="$3" pid command
  if [ ! -f "$pid_file" ]; then
    log "$name 未发现 PID 文件，已是停止状态"
    return 0
  fi

  pid="$(tr -d '[:space:]' < "$pid_file")"
  if ! is_numeric_pid "$pid"; then
    warn "$name PID 文件内容无效，已清理：$pid_file"
    rm -f "$pid_file"
    return 0
  fi
  if ! pid_alive "$pid"; then
    log "$name PID $pid 已不存在，清理陈旧 PID 文件"
    rm -f "$pid_file"
    return 0
  fi

  command="$(pid_command "$pid")"
  if ! pid_owned_by_project "$pid" "$port" "service"; then
    warn "$name PID $pid 无法确认为本项目自有进程（命令行与 cwd 均不匹配），未发送信号：$command"
    return 1
  fi

  log "发送 SIGTERM 到 ${name}（PID ${pid}）..."
  kill -TERM "$pid"
  if wait_until_stopped "$pid"; then
    log "$name 已优雅停止"
    rm -f "$pid_file"
    return 0
  fi

  warn "$name 在 ${STOP_TIMEOUT_SECONDS}s 内未退出，发送 SIGKILL"
  kill -KILL "$pid"
  if wait_until_stopped "$pid"; then
    log "$name 已强制停止"
    rm -f "$pid_file"
    return 0
  fi
  warn "$name PID $pid 仍未退出，请人工检查"
  return 1
}

stop_local_llm() {
  local pid command
  if [ ! -f "$LOCAL_LLM_PID_FILE" ]; then
    log "本地 LLM 未发现 PID 文件，已是停止状态"
    return 0
  fi
  pid="$(tr -d '[:space:]' < "$LOCAL_LLM_PID_FILE")"
  if ! is_numeric_pid "$pid"; then
    warn "本地 LLM PID 文件内容无效，已清理：$LOCAL_LLM_PID_FILE"
    rm -f "$LOCAL_LLM_PID_FILE"
    return 0
  fi
  if ! pid_alive "$pid"; then
    log "本地 LLM PID $pid 已不存在，清理陈旧 PID 文件"
    rm -f "$LOCAL_LLM_PID_FILE"
    return 0
  fi
  command="$(pid_command "$pid")"
  if ! pid_owned_by_project "$pid" "$LOCAL_LLM_PORT" "llm"; then
    warn "本地 LLM PID $pid 无法确认为本项目自带进程（命令行与 cwd 均不匹配），未发送信号：$command"
    return 1
  fi
  log "发送 SIGTERM 到本地 LLM（PID ${pid}）..."
  kill -TERM "$pid"
  if wait_until_stopped "$pid"; then
    log "本地 LLM 已优雅停止"
    rm -f "$LOCAL_LLM_PID_FILE"
    return 0
  fi
  warn "本地 LLM 在 ${STOP_TIMEOUT_SECONDS}s 内未退出，发送 SIGKILL"
  kill -KILL "$pid"
  if wait_until_stopped "$pid"; then
    log "本地 LLM 已强制停止"
    rm -f "$LOCAL_LLM_PID_FILE"
    return 0
  fi
  warn "本地 LLM PID $pid 仍未退出，请人工检查"
  return 1
}

log "停止成都建工 V3.0 服务..."
stop_local_llm
stop_service "IDP" "$IDP_PID_FILE" "$IDP_PORT"
stop_service "RAG" "$RAG_PID_FILE" "$RAG_PORT"
stop_service "Tax" "$TAX_PID_FILE" "$TAX_PORT"

# 兜底：扫描服务端口，回收 PID 文件已丢失但仍占用端口的本项目自有进程。
# 解决"上次异常退出 → .tax.pid 缺失 → 重启时被 prepare_service_slot 拒绝"
# 这一类问题；只杀 cwd 在本项目根下的进程，绝不触及第三方服务。
sweep_orphan_port_holder() {
  local name="$1" port="$2" pid_file="$3" kind="$4"
  command -v lsof >/dev/null 2>&1 || return 0
  local pid
  pid="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -n1 || true)"
  [ -n "$pid" ] || return 0
  is_numeric_pid "$pid" || return 0
  pid_alive "$pid" || return 0
  if pid_owned_by_project "$pid" "$port" "$kind"; then
    log "${name} 端口 ${port} 仍被本项目自有进程占用（PID ${pid}, PID 文件已丢失），执行回收"
    if kill -TERM "$pid" 2>/dev/null; then
      local deadline=$((SECONDS + STOP_TIMEOUT_SECONDS))
      while pid_alive "$pid" && [ "$SECONDS" -lt "$deadline" ]; do
        sleep 1
      done
      if pid_alive "$pid"; then
        warn "${name} PID ${pid} 未响应 SIGTERM，升级为 SIGKILL"
        kill -KILL "$pid" 2>/dev/null || true
        sleep 1
      fi
      rm -f "$pid_file"
    fi
  else
    warn "${name} 端口 ${port} 被非本项目进程占用（PID ${pid}），未发送信号"
    return 1
  fi
}

sweep_orphan_port_holder "本地 LLM" "$LOCAL_LLM_PORT" "$LOCAL_LLM_PID_FILE" "llm" || true
sweep_orphan_port_holder "IDP" "$IDP_PORT" "$IDP_PID_FILE" "service" || true
sweep_orphan_port_holder "RAG" "$RAG_PORT" "$RAG_PID_FILE" "service" || true
sweep_orphan_port_holder "Tax" "$TAX_PORT" "$TAX_PID_FILE" "service" || true

log "停止流程完成（不操作 PostgreSQL 数据或 schema）"
