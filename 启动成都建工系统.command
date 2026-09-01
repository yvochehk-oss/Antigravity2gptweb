#!/bin/zsh
set -u
setopt NULL_GLOB

wait_for_enter() {
  if [[ -t 0 ]]; then
    print -P "\n%F{242}按回车键退出此窗口...%f"
    read -r "? " || true
  fi
}

fail_and_wait() {
  local message="$1" code="${2:-1}"
  print -u2 -P "\n%F{196}❌ 启动失败（退出码 ${code}）：${message}%f"
  wait_for_enter
  return "$code"
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
    [[ -f "${dir}/start_all.sh" && -f "${dir}/stop_all.sh" && -d "${dir}/source_code" ]] || return 1
    print -r -- "$dir"
    return 0
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
  print -P "  %F{214}• 端口 ${port} 有残留进程，先发送 SIGTERM...%f"
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && kill -TERM "$pid" 2>/dev/null || true
  done <<< "$pids"
  for i in 1 2 3 4 5 6 7 8 9 10; do
    port_is_open "$port" || return 0
    sleep 0.2
  done
  pids="$(listeners_on_port "$port")"
  if [[ -n "$pids" ]]; then
    print -P "  %F{196}• 端口 ${port} 未退出，升级为 SIGKILL。%f"
    while IFS= read -r pid; do
      [[ -n "$pid" ]] && kill -KILL "$pid" 2>/dev/null || true
    done <<< "$pids"
  fi
  for i in 1 2 3 4 5; do
    port_is_open "$port" || return 0
    sleep 0.2
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
    kill -TERM "$pid" 2>/dev/null || true
    for i in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$APP_PID_FILE"
}

wait_http() {
  local name="$1" port="$2" url="$3" timeout="${4:-60}"
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if port_is_open "$port" && curl -fsS --connect-timeout 1 --max-time 4 "$url" >/dev/null 2>&1; then
      print -P "  %F{82}✓ ${name}%f  ${url}"
      return 0
    fi
    sleep 1
  done
  print -u2 -P "  %F{196}✗ ${name} 未在 ${timeout}s 内通过健康检查：${url}%f"
  return 1
}

wait_postgres() {
  local timeout="${1:-45}" deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if pg_isready -h 127.0.0.1 -p "$POSTGRES_PORT" >/dev/null 2>&1; then
      print -P "  %F{82}✓ PostgreSQL%f  127.0.0.1:${POSTGRES_PORT}"
      return 0
    fi
    sleep 1
  done
  print -u2 -P "  %F{196}✗ PostgreSQL ${POSTGRES_PORT} 未在 ${timeout}s 内就绪。%f"
  return 1
}

show_failure_logs() {
  local f
  print -u2 -P "\n%F{214}最近运行日志：%f"
  for f in "$BOSS_LOG_FILE" "${PROJECT_DIR}/tax_runtime.log" "${PROJECT_DIR}/rag_runtime.log" "${PROJECT_DIR}/idp_runtime.log" "${PROJECT_DIR}/local_llm_runtime.log"; do
    if [[ -f "$f" ]]; then
      print -u2 -- "---- $(basename -- "$f") ----"
      tail -n 20 "$f" >&2 || true
    fi
  done
}

cleanup_partial_start() {
  local port
  stop_boss_from_pid_file || true
  if [[ -f "$STOP_SCRIPT" ]]; then
    [[ -x "$STOP_SCRIPT" ]] && "$STOP_SCRIPT" >/dev/null 2>&1 || bash "$STOP_SCRIPT" >/dev/null 2>&1 || true
  fi
  for port in "$TAX_PORT" "$RAG_PORT" "$IDP_PORT" "$LOCAL_LLM_PORT" "$BOSS_PORT"; do
    release_port "$port" >/dev/null 2>&1 || true
  done
}

SCRIPT_DIR="$(resolve_script_dir)" || { print -u2 -- "❌ 无法解析启动脚本所在目录。"; exit 1; }
PROJECT_DIR="$(resolve_project_dir)" || {
  print -u2 -- "❌ 无法自动定位成都建工 V3.0 工程根目录。"
  print -u2 -- "   请把脚本放在工程目录/其 scripts 子目录，或设置 CHENGDU_PROJECT_DIR。"
  wait_for_enter
  exit 1
}

START_SCRIPT="${PROJECT_DIR}/start_all.sh"
STOP_SCRIPT="${PROJECT_DIR}/stop_all.sh"
POSTGRES_START_SCRIPT="${PROJECT_DIR}/start_postgres.sh"
APP_DIR="${PROJECT_DIR}/source_code/0.3_老板端安卓App_天府掌舵"
APP_PID_FILE="${PROJECT_DIR}/.app.pid"
BOSS_LOG_FILE="${PROJECT_DIR}/boss_app_runtime.log"
ROOT_ENV_FILE="${PROJECT_DIR}/.env"
RUNTIME_ENV_FILE="${PROJECT_DIR}/.chengdu.env"

configure_path
for env_f in "$ROOT_ENV_FILE" "$RUNTIME_ENV_FILE"; do
  if [[ -f "$env_f" ]]; then
    set -a
    if ! source "$env_f"; then
      set +a
      fail_and_wait "环境文件无法加载：${env_f}" 2
      exit $?
    fi
    set +a
  fi
done

POSTGRES_PORT="${POSTGRES_PORT:-5432}"
LOCAL_LLM_PORT="${LOCAL_LLM_PORT:-8930}"
TAX_PORT="${TAX_PORT:-8921}"
RAG_PORT="${PROJECT_RAG_PORT:-8922}"
IDP_PORT="${IDP_PORT:-8933}"
BOSS_PORT="${BOSS_PORT:-5173}"

for cmd in lsof curl pg_isready node; do
  command -v "$cmd" >/dev/null 2>&1 || { fail_and_wait "缺少必要命令：${cmd}。当前 PATH=${PATH}" 2; exit $?; }
done
[[ -f "$START_SCRIPT" && -f "$POSTGRES_START_SCRIPT" ]] || { fail_and_wait "启动调度文件不完整：${PROJECT_DIR}" 2; exit $?; }
[[ -d "$APP_DIR" ]] || { fail_and_wait "Boss App 目录不存在：${APP_DIR}" 2; exit $?; }
[[ -x "${APP_DIR}/node_modules/.bin/vite" ]] || { fail_and_wait "Boss App 缺少本地 Vite 依赖。请先在 ${APP_DIR} 执行 npm install。" 2; exit $?; }
cd -- "$PROJECT_DIR" || { fail_and_wait "无法进入工程目录：${PROJECT_DIR}" 2; exit $?; }

print -P "%F{39}==================================================================%f"
print -P "%F{39}  🏗️  成都建工 V3.0 · 全系统一键启动%f"
print -P "%F{39}  工程目录：${PROJECT_DIR}%f"
print -P "%F{39}==================================================================%f"

print -P "%F{220}🛑 [1/6] 优雅停止旧服务并释放业务端口...%f"
stop_boss_from_pid_file || true
if [[ -f "$STOP_SCRIPT" ]]; then
  [[ -x "$STOP_SCRIPT" ]] && "$STOP_SCRIPT" >/dev/null 2>&1 || bash "$STOP_SCRIPT" >/dev/null 2>&1 || true
fi
for port in "$TAX_PORT" "$RAG_PORT" "$IDP_PORT" "$LOCAL_LLM_PORT" "$BOSS_PORT"; do
  if ! release_port "$port"; then
    fail_and_wait "端口 ${port} 无法释放，请检查占用进程。" 3
    exit $?
  fi
done
rm -f "${PROJECT_DIR}/.tax.pid" "${PROJECT_DIR}/.rag.pid" "${PROJECT_DIR}/.local_llm.pid" "${PROJECT_DIR}/.app.pid" "${PROJECT_DIR}/.idp.pid"
print -P "%F{82}✅ 旧业务服务已清理。%f"

print -P "%F{220}🗄️  [2/6] 确认 PostgreSQL ${POSTGRES_PORT}...%f"
if ! pg_isready -h 127.0.0.1 -p "$POSTGRES_PORT" >/dev/null 2>&1; then
  [[ -x "$POSTGRES_START_SCRIPT" ]] && "$POSTGRES_START_SCRIPT" || bash "$POSTGRES_START_SCRIPT"
  pg_exit=$?
  if (( pg_exit != 0 )); then
    fail_and_wait "PostgreSQL 启动脚本失败。" "$pg_exit"
    exit $?
  fi
fi
wait_postgres 45 || { fail_and_wait "PostgreSQL 未能就绪。" 4; exit $?; }

print -P "%F{220}🚀 [3/6] 启动 Ling LLM → Tax → RAG → IDP...%f"
typeset -a backend_args
backend_args=(--skip-postgres)
[[ "${CHENGDU_RUN_MIGRATIONS:-1}" == "1" ]] && backend_args+=(--migrate)
backend_args+=("$@")
[[ -x "$START_SCRIPT" ]] && "$START_SCRIPT" "${backend_args[@]}" || bash "$START_SCRIPT" "${backend_args[@]}"
backend_exit=$?
if (( backend_exit != 0 )); then
  show_failure_logs
  cleanup_partial_start
  fail_and_wait "后端核心集群启动失败。" "$backend_exit"
  exit $?
fi

print -P "%F{220}📱 [4/6] 启动老板端移动驾驶舱 ${BOSS_PORT}...%f"
(
  cd -- "$APP_DIR" || exit 1
  if [[ -d dist ]]; then
    nohup "${APP_DIR}/node_modules/.bin/vite" preview --port "$BOSS_PORT" --host 0.0.0.0 >"$BOSS_LOG_FILE" 2>&1 < /dev/null &
  else
    nohup "${APP_DIR}/node_modules/.bin/vite" --port "$BOSS_PORT" --host 0.0.0.0 >"$BOSS_LOG_FILE" 2>&1 < /dev/null &
  fi
  print -r -- "$!" > "$APP_PID_FILE"
)
boss_exit=$?
if (( boss_exit != 0 )); then
  show_failure_logs
  cleanup_partial_start
  fail_and_wait "Boss App 启动命令执行失败。" "$boss_exit"
  exit $?
fi

print -P "%F{220}🔍 [5/6] 等待全部服务达到健康状态...%f"
health_ok=true
wait_postgres 10 || health_ok=false
wait_http "Ling LLM" "$LOCAL_LLM_PORT" "http://127.0.0.1:${LOCAL_LLM_PORT}/health" 30 || health_ok=false
wait_http "Tax" "$TAX_PORT" "http://127.0.0.1:${TAX_PORT}/healthz" 30 || health_ok=false
wait_http "RAG" "$RAG_PORT" "http://127.0.0.1:${RAG_PORT}/api/v1/health" 60 || health_ok=false
case "${IDP_ENABLED:-1}" in
  0|false|FALSE|no|NO|off|OFF) print -P "  %F{214}– IDP 已由配置禁用%f" ;;
  *) wait_http "IDP" "$IDP_PORT" "http://127.0.0.1:${IDP_PORT}/health" 30 || health_ok=false ;;
esac
wait_http "Boss App" "$BOSS_PORT" "http://127.0.0.1:${BOSS_PORT}/" 30 || health_ok=false
if [[ "$health_ok" != true ]]; then
  show_failure_logs
  cleanup_partial_start
  fail_and_wait "至少一个服务未通过健康检查，已回收本次业务进程。" 6
  exit $?
fi

print -P "%F{220}🌐 [6/6] 全部健康，打开老板端主页...%f"
BOSS_URL="http://127.0.0.1:${BOSS_PORT}"
[[ "${CHENGDU_NO_BROWSER:-0}" != "1" ]] && open "$BOSS_URL" >/dev/null 2>&1 || true

print -- ""
print -P "%F{46}==================================================================%f"
print -P "%F{46}  🎉 成都建工 V3.0 全系统已就绪%f"
print -P "%F{46}==================================================================%f"
print -P "  ✅ PostgreSQL             127.0.0.1:${POSTGRES_PORT}"
print -P "  ✅ Ling-3.0 LLM           http://127.0.0.1:${LOCAL_LLM_PORT}/v1"
print -P "  ✅ Tax 税务中台            http://127.0.0.1:${TAX_PORT}"
print -P "  ✅ RAG 知识库              http://127.0.0.1:${RAG_PORT}"
case "${IDP_ENABLED:-1}" in
  0|false|FALSE|no|NO|off|OFF) print -P "  ⚪ IDP 文档录入引擎         disabled" ;;
  *) print -P "  ✅ IDP 文档录入引擎         http://127.0.0.1:${IDP_PORT}" ;;
esac
print -P "  ✅ Boss 移动驾驶舱          ${BOSS_URL}"
print -P "%F{242}------------------------------------------------------------------%f"
print -P "  📚 Tax OpenAPI            http://127.0.0.1:${TAX_PORT}/docs"
print -P "  📂 工程目录                ${PROJECT_DIR}"
print -P "  🧾 Boss 日志               ${BOSS_LOG_FILE}"
print -P "  🛑 停止：双击【停止成都建工系统.command】"
print -P "%F{46}==================================================================%f"
print -- ""
wait_for_enter
exit 0
