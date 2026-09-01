#!/bin/zsh

# ==============================================================================
# 成都建工 V3.0 · macOS 桌面一键启动器
# PostgreSQL 5432 → Ling LLM 8930 → Tax 8921 → RAG 8922 → IDP 8933 → Boss 5173
# ==============================================================================
set -u

wait_for_enter() {
  if [[ -t 0 ]]; then
    print -P "\n%F{242}按回车键退出此窗口...%f"
    read -r "? " || true
  fi
}

fail_and_wait() {
  local message="$1"
  local code="${2:-1}"
  print -u2 -P "\n%F{196}❌ 启动失败（退出码 ${code}）：${message}%f"
  wait_for_enter
  return "$code"
}

resolve_script_dir() {
  local source="$0"
  local base target
  while [[ -L "$source" ]]; do
    base="$(cd -- "$(dirname -- "$source")" && pwd -P)" || return 1
    target="$(readlink "$source")" || return 1
    if [[ "$target" = /* ]]; then
      source="$target"
    else
      source="${base}/${target}"
    fi
  done
  cd -- "$(dirname -- "$source")" && pwd -P
}

resolve_project_dir() {
  local dir parent i
  if [[ -n "${CHENGDU_PROJECT_DIR:-}" ]]; then
    dir="$(cd -- "${CHENGDU_PROJECT_DIR}" 2>/dev/null && pwd -P)" || return 1
    if [[ -f "${dir}/start_all.sh" && -f "${dir}/stop_all.sh" && -d "${dir}/source_code" ]]; then
      print -r -- "$dir"
      return 0
    fi
    return 1
  fi
  dir="$SCRIPT_DIR"
  for i in 1 2 3 4 5 6; do
    if [[ -f "${dir}/start_all.sh" && -f "${dir}/stop_all.sh" && -d "${dir}/source_code" ]]; then
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
  prepend_path_if_dir "/opt/homebrew/bin"
  prepend_path_if_dir "/opt/homebrew/sbin"
  prepend_path_if_dir "/opt/homebrew/opt/llama.cpp/bin"
  prepend_path_if_dir "/usr/local/bin"
  prepend_path_if_dir "/usr/local/sbin"
  prepend_path_if_dir "${HOME}/.local/bin"
  prepend_path_if_dir "${HOME}/.cargo/bin"
  prepend_path_if_dir "${HOME}/.volta/bin"
  prepend_path_if_dir "${HOME}/.fnm"
  prepend_path_if_dir "${HOME}/llama.cpp/build/bin"
  local dir
  for dir in /opt/homebrew/opt/postgresql@*/bin(N) /usr/local/opt/postgresql@*/bin(N); do prepend_path_if_dir "$dir"; done
  for dir in "${HOME}"/.nvm/versions/node/*/bin(N); do prepend_path_if_dir "$dir"; done
}

listeners_on_port() { lsof -t -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true; }
port_is_open() { if command -v nc >/dev/null 2>&1; then nc -z 127.0.0.1 "$1" >/dev/null 2>&1; else [[ -n "$(listeners_on_port "$1")" ]]; fi; }
release_port() {
  local port="$1" pids pid i
  pids="$(listeners_on_port "$port")"
  [[ -z "$pids" ]] && return 0
  while IFS= read -r pid; do [[ -n "$pid" ]] && kill -TERM "$pid" 2>/dev/null || true; done <<< "$pids"
  for i in 1 2 3 4 5 6 7 8 9 10; do port_is_open "$port" || return 0; sleep 0.2; done
  pids="$(listeners_on_port "$port")"
  while IFS= read -r pid; do [[ -n "$pid" ]] && kill -KILL "$pid" 2>/dev/null || true; done <<< "$pids"
  for i in 1 2 3 4 5; do port_is_open "$port" || return 0; sleep 0.2; done
  return 1
}

stop_boss_from_pid_file() {
  [[ -f "$APP_PID_FILE" ]] || return 0
  local pid cwd i
  pid="$(tr -d '[:space:]' < "$APP_PID_FILE" 2>/dev/null || true)"
  case "$pid" in ''|*[!0-9]*) rm -f "$APP_PID_FILE"; return 0 ;; esac
  kill -0 "$pid" 2>/dev/null || { rm -f "$APP_PID_FILE"; return 0; }
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
  if [[ -n "$cwd" && "$cwd" == "$APP_DIR"* ]]; then
    kill -TERM "$pid" 2>/dev/null || true
    for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$APP_PID_FILE"
}

wait_http() {
  local name="$1" port="$2" url="$3" timeout="${4:-60}" deadline=$((SECONDS + ${4:-60}))
  while (( SECONDS < deadline )); do
    if port_is_open "$port" && curl -fsS --connect-timeout 1 --max-time 4 "$url" >/dev/null 2>&1; then print -P "  %F{82}✓ ${name}%f  ${url}"; return 0; fi
    sleep 1
  done
  print -u2 -P "  %F{196}✗ ${name} 未通过健康检查：${url}%f"
  return 1
}

wait_postgres() {
  local timeout="${1:-45}" deadline=$((SECONDS + ${1:-45}))
  while (( SECONDS < deadline )); do pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && { print -P "  %F{82}✓ PostgreSQL%f  127.0.0.1:5432"; return 0; }; sleep 1; done
  return 1
}

show_failure_logs() {
  local f
  for f in "$BOSS_LOG_FILE" "${PROJECT_DIR}/tax_runtime.log" "${PROJECT_DIR}/rag_runtime.log" "${PROJECT_DIR}/idp_runtime.log" "${PROJECT_DIR}/local_llm_runtime.log"; do [[ -f "$f" ]] && { print -u2 -- "---- ${f:t} ----"; tail -n 20 "$f" >&2 || true; }; done
}

cleanup_partial_start() {
  stop_boss_from_pid_file || true
  [[ -f "$STOP_SCRIPT" ]] && { [[ -x "$STOP_SCRIPT" ]] && "$STOP_SCRIPT" >/dev/null 2>&1 || bash "$STOP_SCRIPT" >/dev/null 2>&1 || true; }
  local port
  for port in 8921 8922 8933 8930 5173 3000; do release_port "$port" >/dev/null 2>&1 || true; done
}

SCRIPT_DIR="$(resolve_script_dir)" || exit 1
PROJECT_DIR="$(resolve_project_dir)" || { print -u2 -- "❌ 无法自动定位成都建工 V3.0 工程根目录。"; wait_for_enter; exit 1; }
START_SCRIPT="${PROJECT_DIR}/start_all.sh"
STOP_SCRIPT="${PROJECT_DIR}/stop_all.sh"
POSTGRES_START_SCRIPT="${PROJECT_DIR}/start_postgres.sh"
APP_DIR="${PROJECT_DIR}/source_code/0.3_老板端安卓App_天府掌舵"
APP_PID_FILE="${PROJECT_DIR}/.app.pid"
BOSS_LOG_FILE="${PROJECT_DIR}/boss_app_runtime.log"
ROOT_ENV_FILE="${PROJECT_DIR}/.env"
RUNTIME_ENV_FILE="${PROJECT_DIR}/.chengdu.env"
configure_path
for env_f in "$ROOT_ENV_FILE" "$RUNTIME_ENV_FILE"; do [[ -f "$env_f" ]] && { set -a; source "$env_f" || { set +a; fail_and_wait "环境文件无法加载：${env_f}" 2; exit $?; }; set +a; }; done
for cmd in lsof curl pg_isready node npx; do command -v "$cmd" >/dev/null 2>&1 || { fail_and_wait "缺少必要命令：${cmd}" 2; exit $?; }; done
cd -- "$PROJECT_DIR" || exit 2

print -P "%F{39}==================================================================%f"
print -P "%F{39}  🏗️  成都建工 V3.0 · 全系统一键启动%f"
print -P "%F{39}==================================================================%f"
stop_boss_from_pid_file || true
[[ -f "$STOP_SCRIPT" ]] && { [[ -x "$STOP_SCRIPT" ]] && "$STOP_SCRIPT" >/dev/null 2>&1 || bash "$STOP_SCRIPT" >/dev/null 2>&1 || true; }
for port in 8921 8922 8933 8930 5173 3000; do release_port "$port" || { fail_and_wait "端口 ${port} 无法释放" 3; exit $?; }; done
rm -f "${PROJECT_DIR}/.tax.pid" "${PROJECT_DIR}/.rag.pid" "${PROJECT_DIR}/.local_llm.pid" "${PROJECT_DIR}/.app.pid" "${PROJECT_DIR}/.idp.pid"

[[ -x "$POSTGRES_START_SCRIPT" ]] && "$POSTGRES_START_SCRIPT" || bash "$POSTGRES_START_SCRIPT"
pg_exit=$?
(( pg_exit == 0 )) && wait_postgres 45 || { fail_and_wait "PostgreSQL 未能就绪" 4; exit $?; }

typeset -a backend_args
backend_args=(--skip-postgres)
[[ "${CHENGDU_RUN_MIGRATIONS:-1}" == "1" ]] && backend_args+=(--migrate)
backend_args+=("$@")
[[ -x "$START_SCRIPT" ]] && "$START_SCRIPT" "${backend_args[@]}" || bash "$START_SCRIPT" "${backend_args[@]}"
backend_exit=$?
(( backend_exit == 0 )) || { show_failure_logs; cleanup_partial_start; fail_and_wait "后端核心集群启动失败" "$backend_exit"; exit $?; }

[[ -d "$APP_DIR" && -x "${APP_DIR}/node_modules/.bin/vite" ]] || { cleanup_partial_start; fail_and_wait "Boss App 缺少目录或 Vite 依赖" 5; exit $?; }
(
  cd -- "$APP_DIR" || exit 1
  if [[ -d dist ]]; then nohup "${APP_DIR}/node_modules/.bin/vite" preview --port 5173 --host 0.0.0.0 >"$BOSS_LOG_FILE" 2>&1 < /dev/null &
  else nohup "${APP_DIR}/node_modules/.bin/vite" --port 5173 --host 0.0.0.0 >"$BOSS_LOG_FILE" 2>&1 < /dev/null & fi
  print -r -- "$!" > "$APP_PID_FILE"
)

health_ok=true
wait_postgres 10 || health_ok=false
wait_http "Ling LLM" 8930 "http://127.0.0.1:8930/health" 20 || health_ok=false
wait_http "Tax" 8921 "http://127.0.0.1:8921/healthz" 30 || health_ok=false
wait_http "RAG" 8922 "http://127.0.0.1:8922/api/v1/health" 45 || health_ok=false
case "${IDP_ENABLED:-1}" in 0|false|FALSE|no|NO|off|OFF) ;; *) wait_http "IDP" 8933 "http://127.0.0.1:8933/health" 30 || health_ok=false ;; esac
wait_http "Boss App" 5173 "http://127.0.0.1:5173/" 30 || health_ok=false
[[ "$health_ok" == true ]] || { show_failure_logs; cleanup_partial_start; fail_and_wait "至少一个服务未通过健康检查" 6; exit $?; }

[[ "${CHENGDU_NO_BROWSER:-0}" == "1" ]] || open "http://127.0.0.1:5173" >/dev/null 2>&1 || true
print -P "%F{46}==================================================================%f"
print -P "%F{46}  🎉 成都建工 V3.0 全系统已就绪%f"
print -P "  ✅ PostgreSQL 5432 | Ling 8930 | Tax 8921 | RAG 8922 | IDP 8933 | Boss 5173"
print -P "  📱 http://127.0.0.1:5173"
print -P "%F{46}==================================================================%f"
wait_for_enter
exit 0
