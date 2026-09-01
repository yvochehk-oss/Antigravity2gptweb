#!/bin/zsh

# ==============================================================================
# 成都建工 V3.0 财税智控与 IDP 穿透中枢 · 桌面一键启动终端
# ==============================================================================
set -u

# 1. 注入标准环境路径，确保双击时能正确找到 Homebrew, uv, Node, npx, psql, llama-server 等命令
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:${HOME}/.local/bin:${HOME}/.cargo/bin:${HOME}/Library/Python/3.12/bin:${PATH}"

# 2. 动态解析工程根目录（自适应任意移动位置）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${CHENGDU_PROJECT_DIR:-${SCRIPT_DIR}}"

START_SCRIPT="${PROJECT_DIR}/start_all.sh"
STOP_SCRIPT="${PROJECT_DIR}/stop_all.sh"
APP_DIR="${PROJECT_DIR}/source_code/0.3_老板端安卓App_天府掌舵"
APP_PID_FILE="${PROJECT_DIR}/.app.pid"
IDP_DIR="${PROJECT_DIR}/source_code/0.4_IDP文档录入引擎_V3.0"
IDP_PID_FILE="${PROJECT_DIR}/.idp.pid"
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

fail_and_wait() {
  local message="$1"
  local code="${2:-1}"
  print -u2 -P "\n%F{196}❌ 启动失败（退出码 ${code}）：${message}%f"
  wait_for_enter
  return "$code"
}

check_port_open() {
  local port="$1"
  nc -z 127.0.0.1 "$port" >/dev/null 2>&1
}

print -P "%F{39}==================================================================%f"
print -P "%F{39}  🏗️  成都建工 V3.0 财税智控与 IDP 穿透中枢 · 正在启动...%f"
print -P "%F{39}==================================================================%f"

# 3. 检查并进入目录
if [[ ! -d "$PROJECT_DIR" ]]; then
  fail_and_wait "无法定位系统工程目录：${PROJECT_DIR}" 1
  exit $?
fi

if ! cd -- "$PROJECT_DIR"; then
  fail_and_wait "无法进入系统工程目录：${PROJECT_DIR}" 1
  exit $?
fi

# 4. 启动前优雅停止旧服务并释放业务端口（8921, 8922, 8933, 8930, 5173, 3000）
print -P "%F{220}🛑 [1/5] 正在检查并释放历史残留服务与端口...%f"

if [[ -f "$STOP_SCRIPT" ]]; then
  if [[ -x "$STOP_SCRIPT" ]]; then
    "$STOP_SCRIPT" >/dev/null 2>&1 || true
  else
    zsh "$STOP_SCRIPT" >/dev/null 2>&1 || true
  fi
fi

# 强制清理业务端口（注意：保护 5432 PostgreSQL，不盲杀）
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

# 清理 PID 文件
rm -f "${PROJECT_DIR}/.tax.pid" "${PROJECT_DIR}/.rag.pid" "${PROJECT_DIR}/.local_llm.pid" "${PROJECT_DIR}/.app.pid" "${PROJECT_DIR}/.idp.pid"
sleep 1
print -P "%F{82}✅ 端口已就绪。%f"

# 5. 启动后端核心集群 (PostgreSQL 5432 + Tax 8921 + RAG 8922 + IDP 8933 + Ling LLM 8930)
if [[ ! -f "$START_SCRIPT" ]]; then
  fail_and_wait "找不到启动调度脚本：${START_SCRIPT}" 1
  exit $?
fi

print -P "%F{220}🚀 [2/5] 正在拉起后端集群 (PostgreSQL + Tax + RAG + IDP + Ling)...%f"
if [[ -x "$START_SCRIPT" ]]; then
  "$START_SCRIPT" --migrate "$@"
else
  zsh "$START_SCRIPT" --migrate "$@"
fi
start_exit=$?

if (( start_exit != 0 )); then
  fail_and_wait "后端核心集群启动异常" "$start_exit"
  exit $start_exit
fi

# 6. 启动老板端移动驾驶舱前端 (端口 5173)
if [[ -d "$APP_DIR" ]]; then
  print -P "%F{220}📱 [3/5] 正在启动老板端移动驾驶舱前端 (端口 5173)...%f"
  (
    cd "$APP_DIR"
    # 如果有构建产物优先 preview，否则 dev 模式启动
    if [[ -d "dist" ]]; then
      nohup npx vite preview --port 5173 --host 0.0.0.0 > /tmp/boss_app.log 2>&1 &
    else
      nohup npx vite --port 5173 --host 0.0.0.0 > /tmp/boss_app.log 2>&1 &
    fi
    echo $! > "$APP_PID_FILE"
  )
fi

# 7. 服务健康探测与就绪轮询
print -P "%F{220}🔍 [4/5] 正在进行全链路健康与端口就绪探测...%f"
max_attempts=15
attempt=0
tax_ready=false
rag_ready=false
idp_ready=false
app_ready=false

while (( attempt < max_attempts )); do
  (( attempt++ ))
  check_port_open 8921 && tax_ready=true
  check_port_open 8922 && rag_ready=true
  check_port_open 8933 && idp_ready=true
  check_port_open 5173 && app_ready=true
  
  if [[ "$tax_ready" == "true" && "$rag_ready" == "true" && "$idp_ready" == "true" && "$app_ready" == "true" ]]; then
    break
  fi
  sleep 1
done

# 8. 自动在默认浏览器中打开系统入口
print -P "%F{220}🌐 [5/5] 正在自动开启浏览器视图...%f"
if [[ "${CHENGDU_NO_BROWSER:-0}" != "1" ]]; then
  open "http://127.0.0.1:5173" 2>/dev/null || true
  sleep 0.5
  open "http://127.0.0.1:8921" 2>/dev/null || true
fi

# 9. 打印完整服务入口看板
print -- ""
print -P "%F{46}==================================================================%f"
print -P "%F{46}  🎉 成都建工 V3.0 全系统已成功拉起并处于活跃状态！%f"
print -P "%F{46}==================================================================%f"
print -P "  %F{39}【核心业务访问入口】%f"
print -P "  📱 %B%F{226}老板端移动驾驶舱:%f%b    http://127.0.0.1:5173  %F{82}(推荐·已在浏览器打开)%f"
print -P "  🏢 %B%F{226}税务管理中台 Web:%f%b    http://127.0.0.1:8921  %F{82}(已在浏览器打开)%f"
print -P "  🧠 %B%F{226}RAG 知识证据中枢:%f%b    http://127.0.0.1:8922"
print -P "  📄 %B%F{226}IDP 文档录入与审计:%f%b  http://127.0.0.1:8933"
print -P "  🤖 %B%F{226}Ling-3.0 办事员接口:%f%b http://127.0.0.1:8930/v1"
print -P "  🗄️ %B%F{226}PostgreSQL 数据库:%f%b   127.0.0.1:5432 (projectrag)"
print -P "%F{242}------------------------------------------------------------------%f"
print -P "  %F{226}🔑 默认演示登录账号：%f admin  /  密码：密码同用户名或直接点击演示登录"
print -P "  %F{226}📚 OpenAPI / 接口文档：%f http://127.0.0.1:8921/docs"
print -P "%F{242}------------------------------------------------------------------%f"
print -P "  💡 %F{220}提示：请保持本终端窗口开启，服务将在后台持续提供服务。%f"
print -P "  🛑 %F{196}停止服务：%f"
print -P "     • 可直接双击桌面【%B停止成都建工系统.command%b】"
print -P "     • 或在终端执行: %B${PROJECT_DIR}/stop_all.sh%b"
print -P "%F{46}==================================================================%f"
print -- ""

wait_for_enter
exit 0

