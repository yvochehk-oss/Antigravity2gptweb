#!/bin/zsh
set -u
setopt NULL_GLOB

wait_for_enter() {
  if [[ -t 0 ]]; then
    print -P "\n%F{242}按回车键退出此窗口...%f"
    read -r "? " || true
  fi
}

resolve_script_dir() {
  local source="$0" base target
  while [[ -L "$source" ]]; do
    base="$(cd -- "$(dirname -- "$source")" && pwd -P)" || return 1
    target="$(readlink "$source")" || return 1
    [[ "$target" = /* ]] && source="$target" || source="${base}/${target}"
  done
  cd -- "$(dirname -- "$source")" && pwd -P
}

resolve_project_dir() {
  local dir parent i
  if [[ -n "${CHENGDU_PROJECT_DIR:-}" ]]; then
    dir="$(cd -- "${CHENGDU_PROJECT_DIR}" 2>/dev/null && pwd -P)" || return 1
    [[ -f "${dir}/stop_all.sh" && -d "${dir}/source_code" ]] || return 1
    print -r -- "$dir"
    return 0
  fi
  dir="$SCRIPT_DIR"
  for i in 1 2 3 4 5 6; do
    if [[ -f "${dir}/stop_all.sh" && -d "${dir}/source_code" ]]; then
      print -r -- "$dir"
      return 0
    fi
    parent="$(dirname -- "$dir")"
    [[ "$parent" == "$dir" ]] && break
    dir="$parent"
  done
  return 1
}

prepend_path_if_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  case ":${PATH}:" in
    *":${dir}:"*) ;;
    *) export PATH="${dir}:${PATH}" ;;
  esac
}

configure_path() {
  local dir
  prepend_path_if_dir "/opt/homebrew/bin"
  prepend_path_if_dir "/opt/homebrew/sbin"
  prepend_path_if_dir "/opt/homebrew/opt/llama.cpp/bin"
  prepend_path_if_dir "/usr/local/bin"
  prepend_path_if_dir "/usr/local/sbin"
  prepend_path_if_dir "${HOME}/.local/bin"
  prepend_path_if_dir "${HOME}/.cargo/bin"
  prepend_path_if_dir "${HOME}/.volta/bin"
  prepend_path_if_dir "${HOME}/llama.cpp/build/bin"
  for dir in /opt/homebrew/opt/postgresql@*/bin /usr/local/opt/postgresql@*/bin; do
    prepend_path_if_dir "$dir"
  done
  for dir in "${HOME}"/.nvm/versions/node/*/bin; do
    prepend_path_if_dir "$dir"
  done
}

listeners_on_port() {
  local port="$1"
  command -v lsof >/dev/null 2>&1 || return 1
  lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

port_is_open() {
  local port="$1"
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
  else
    [[ -n "$(listeners_on_port "$port")" ]]
  fi
}

release_port() {
  local port="$1" pids pid i
  pids="$(listeners_on_port "$port")"
  [[ -z "$pids" ]] && return 0
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && kill -TERM "$pid" 2>/dev/null || true
  done <<< "$pids"
  for i in 1 2 3 4 5 6 7 8 9 10; do
    port_is_open "$port" || return 0
    sleep 0.15
  done
  pids="$(listeners_on_port "$port")"
  if [[ -n "$pids" ]]; then
    while IFS= read -r pid; do
      [[ -n "$pid" ]] && kill -KILL "$pid" 2>/dev/null || true
    done <<< "$pids"
  fi
  for i in 1 2 3 4 5; do
    port_is_open "$port" || return 0
    sleep 0.15
  done
  return 1
}

stop_boss_from_pid_file() {
  [[ -f "$APP_PID_FILE" ]] || return 0
  local pid cwd i
  pid="$(tr -d '[:space:]' < "$APP_PID_FILE" 2>/dev/null || true)"
  case "$pid" in
    ''|*[!0-9]*) rm -f "$APP_PID_FILE"; return 0 ;;
  esac
  kill -0 "$pid" 2>/dev/null || { rm -f "$APP_PID_FILE"; return 0; }
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
  if [[ -n "$cwd" && "$cwd" == "$APP_DIR"* ]]; then
    print -P "  %F{220}• Boss App PID ${pid}：SIGTERM%f"
    kill -TERM "$pid" 2>/dev/null || true
    for i in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.15
    done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
  else
    print -P "  %F{214}⚠ .app.pid 的 PID ${pid} 无法确认属于 Boss App，不盲杀该 PID。%f"
  fi
  rm -f "$APP_PID_FILE"
}

project_postgres_running() {
  local pid cmd
  [[ -f "${POSTGRES_DATA_DIR}/postmaster.pid" ]] || return 1
  pid="$(head -n 1 "${POSTGRES_DATA_DIR}/postmaster.pid" 2>/dev/null || true)"
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$pid" 2>/dev/null || return 1
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$cmd" == *"$POSTGRES_DATA_DIR"* ]]
}

stop_project_postgres() {
  if ! project_postgres_running; then
    print -P "  %F{242}• 本项目 PostgreSQL 未运行或 ${POSTGRES_PORT} 属于外部实例，跳过。%f"
    return 0
  fi
  print -P "  %F{220}• 正在停止本项目 PostgreSQL...%f"
  if [[ -x "$POSTGRES_STOP_SCRIPT" ]]; then
    "$POSTGRES_STOP_SCRIPT" >/dev/null 2>&1 || true
  elif [[ -f "$POSTGRES_STOP_SCRIPT" ]]; then
    bash "$POSTGRES_STOP_SCRIPT" >/dev/null 2>&1 || true
  elif command -v pg_ctl >/dev/null 2>&1; then
    pg_ctl -D "$POSTGRES_DATA_DIR" stop -m fast >/dev/null 2>&1 || true
  fi
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    project_postgres_running || return 0
    sleep 0.2
  done
  return 1
}

SCRIPT_DIR="$(resolve_script_dir)" || { print -u2 -- "❌ 无法解析停止脚本所在目录。"; exit 1; }
PROJECT_DIR="$(resolve_project_dir)" || {
  print -u2 -- "❌ 无法自动定位成都建工 V3.0 工程根目录。"
  print -u2 -- "   请把脚本放在工程目录/其 scripts 子目录，或设置 CHENGDU_PROJECT_DIR。"
  wait_for_enter
  exit 1
}

STOP_SCRIPT="${PROJECT_DIR}/stop_all.sh"
POSTGRES_STOP_SCRIPT="${PROJECT_DIR}/stop_postgres.sh"
POSTGRES_DATA_DIR="${PROJECT_DIR}/data/postgres"
APP_DIR="${PROJECT_DIR}/source_code/0.3_老板端安卓App_天府掌舵"
APP_PID_FILE="${PROJECT_DIR}/.app.pid"
ROOT_ENV_FILE="${PROJECT_DIR}/.env"
RUNTIME_ENV_FILE="${PROJECT_DIR}/.chengdu.env"

configure_path
for env_f in "$ROOT_ENV_FILE" "$RUNTIME_ENV_FILE"; do
  if [[ -f "$env_f" ]]; then
    set -a
    source "$env_f" || print -u2 -- "⚠ 环境文件加载失败：${env_f}，继续执行停止清理。"
    set +a
  fi
done

POSTGRES_PORT="${POSTGRES_PORT:-5432}"
LOCAL_LLM_PORT="${LOCAL_LLM_PORT:-8930}"
TAX_PORT="${TAX_PORT:-8921}"
RAG_PORT="${PROJECT_RAG_PORT:-8922}"
IDP_PORT="${IDP_PORT:-8933}"
BOSS_PORT="${BOSS_PORT:-5173}"

print -P "%F{196}==================================================================%f"
print -P "%F{196}  🛑 成都建工 V3.0 · 全系统一键停止%f"
print -P "%F{196}  工程目录：${PROJECT_DIR}%f"
print -P "%F{196}==================================================================%f"

all_clear=true
print -P "%F{220}⏳ [1/4] 停止 Boss App...%f"
stop_boss_from_pid_file || all_clear=false

print -P "%F{220}⏳ [2/4] 优雅停止 IDP / RAG / Tax / Ling...%f"
if [[ -f "$STOP_SCRIPT" ]]; then
  [[ -x "$STOP_SCRIPT" ]] && "$STOP_SCRIPT" || bash "$STOP_SCRIPT" || all_clear=false
fi

print -P "%F{220}🧹 [3/4] 清理业务端口残留...%f"
for port in "$TAX_PORT" "$RAG_PORT" "$IDP_PORT" "$LOCAL_LLM_PORT" "$BOSS_PORT"; do
  if ! release_port "$port"; then
    print -u2 -P "  %F{196}⚠ 端口 ${port} 仍未释放。%f"
    all_clear=false
  fi
done
rm -f "${PROJECT_DIR}/.tax.pid" "${PROJECT_DIR}/.rag.pid" "${PROJECT_DIR}/.local_llm.pid" "${PROJECT_DIR}/.app.pid" "${PROJECT_DIR}/.idp.pid"

print -P "%F{220}🗄️  [4/4] PostgreSQL 策略...%f"
if [[ "${CHENGDU_STOP_POSTGRES:-0}" == "1" ]]; then
  if ! stop_project_postgres; then
    print -u2 -P "  %F{196}⚠ 本项目 PostgreSQL 未能完全停止。%f"
    all_clear=false
  fi
else
  print -P "  %F{82}• PostgreSQL ${POSTGRES_PORT} 默认保持运行（设置 CHENGDU_STOP_POSTGRES=1 可一并停止）。%f"
fi

for port in "$TAX_PORT" "$RAG_PORT" "$IDP_PORT" "$LOCAL_LLM_PORT" "$BOSS_PORT"; do
  if port_is_open "$port"; then
    print -u2 -P "  %F{196}⚠ 端口 ${port} 仍有监听。%f"
    all_clear=false
  fi
done

print -- ""
if [[ "$all_clear" == true ]]; then
  print -P "%F{46}==================================================================%f"
  print -P "%F{46}  ✅ 成都建工 V3.0 已清爽停止：全部业务端口与后台进程已释放%f"
  print -P "%F{46}==================================================================%f"
else
  print -P "%F{220}==================================================================%f"
  print -P "%F{220}  ⚠️ 停止流程完成，但仍有项目需要人工检查%f"
  print -P "%F{220}==================================================================%f"
fi

if [[ "${CHENGDU_STOP_POSTGRES:-0}" == "1" ]]; then
  project_postgres_running && print -P "  🗄️ PostgreSQL：%F{196}仍运行%f" || print -P "  🗄️ PostgreSQL：已停止 / 非本项目实例不处理"
else
  print -P "  🗄️ PostgreSQL：保持运行（${POSTGRES_PORT}）"
fi
print -- ""
wait_for_enter
[[ "$all_clear" == true ]] && exit 0 || exit 1
