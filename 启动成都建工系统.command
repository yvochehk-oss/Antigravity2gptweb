#!/bin/zsh

# ==============================================================================
# 成都建工 V2.0 财税智控与 RAG 穿透系统 · 桌面一键启动终端
# ==============================================================================
set -u

# 1. 注入标准环境路径，确保双击时能正确找到 uv, node, npx, psql 等命令
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"

PROJECT_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0"
START_SCRIPT="${PROJECT_DIR}/start_all.sh"
STOP_SCRIPT="${PROJECT_DIR}/stop_all.sh"
APP_DIR="${PROJECT_DIR}/source_code/0.3_老板端安卓App_天府掌舵"
APP_PID_FILE="${PROJECT_DIR}/.app.pid"

wait_for_enter() {
  if [[ -t 0 ]]; then
    read -r "?按回车键关闭此窗口..." || true
  fi
}

fail_and_wait() {
  local message="$1"
  local code="${2:-1}"
  print -u2 -- "\n❌ 启动失败（退出码 ${code}）：${message}"
  wait_for_enter
  return "$code"
}

print -- "=================================================================="
print -- "  🏗️  成都建工 V2.0 财税智控与 RAG 穿透系统 · 正在启动..."
print -- "=================================================================="

# 2. 检查目录
if [[ ! -d "$PROJECT_DIR" ]]; then
  fail_and_wait "无法定位系统工程目录：${PROJECT_DIR}" 1
  exit $?
fi

if ! cd -- "$PROJECT_DIR"; then
  fail_and_wait "无法进入系统工程目录：${PROJECT_DIR}" 1
  exit $?
fi

# 3. 启动前强制关掉之前的全部旧服务（确保端口 8921, 8922, 5173, 8930 与 PID 完全释放）
print -- "🛑 正在关掉之前的旧服务并清理端口占用..."

# (1) 先执行优雅停止脚本
if [[ -f "$STOP_SCRIPT" ]]; then
  if [[ -x "$STOP_SCRIPT" ]]; then
    "$STOP_SCRIPT" >/dev/null 2>&1 || true
  else
    zsh "$STOP_SCRIPT" >/dev/null 2>&1 || true
  fi
fi

# (2) 强制清理占用 8921(Tax), 8922(RAG), 5173(App), 8930(LLM) 端口的残留进程
for port in 8921 8922 5173 8930; do
  pids=$(lsof -t -i:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    kill -9 $pids 2>/dev/null || true
  fi
done

# (3) 清理所有 PID 记录文件
rm -f "${PROJECT_DIR}/.tax.pid" "${PROJECT_DIR}/.rag.pid" "${PROJECT_DIR}/.local_llm.pid" "${PROJECT_DIR}/.app.pid"
sleep 1
print -- "✅ 之前的服务已全部关闭，端口已释放。"

# 4. 启动后端核心集群 (Tax 8921 + RAG 8922 + PostgreSQL 5432 + Local LLM 8930)
if [[ ! -f "$START_SCRIPT" ]]; then
  fail_and_wait "找不到启动脚本：${START_SCRIPT}" 1
  exit $?
fi

print -- "🚀 正在拉起后端核心集群 (Tax + RAG + PostgreSQL)..."
if [[ -x "$START_SCRIPT" ]]; then
  "$START_SCRIPT" "$@"
else
  zsh "$START_SCRIPT" "$@"
fi
start_exit=$?

if (( start_exit != 0 )); then
  fail_and_wait "后端核心服务启动异常" "$start_exit"
  exit $start_exit
fi

# 5. 启动老板端移动驾驶舱前端预览 (端口 5173)
if [[ -d "$APP_DIR" ]]; then
  print -- "📱 正在启动老板端移动驾驶舱 (端口 5173)..."
  (
    cd "$APP_DIR" && nohup npx vite preview --port 5173 --host 0.0.0.0 > /tmp/boss_app.log 2>&1 &
    echo $! > "$APP_PID_FILE"
  )
  sleep 2
fi

# 6. 自动唤起默认浏览器访问系统
print -- "🌐 正在自动为您打开系统页面..."
sleep 1
open "http://127.0.0.1:5173" 2>/dev/null || true
open "http://127.0.0.1:8921" 2>/dev/null || true

# 7. 打印完整服务入口看板
print -- ""
print -- "=================================================================="
print -- "  🎉 成都建工 V2.0 全系统全部就绪！"
print -- "=================================================================="
print -- "  【核心访问入口】"
print -- "  📱 移动端驾驶舱 (天府掌舵):  http://127.0.0.1:5173  (推荐)"
print -- "  🏢 税务与财税管理中台 (Web):  http://127.0.0.1:8921"
print -- "  🧠 RAG 知识证据与智能研判:    http://127.0.0.1:8922"
print -- "  🤖 本地保底大模型接口:        http://127.0.0.1:8930/v1"
print -- "  🗄️ PostgreSQL 数据库服务:    127.0.0.1:5432 (库名: projectrag)"
print -- "------------------------------------------------------------------"
print -- "  【默认登录身份】"
print -- "  • 管理员账号: admin     密码: 888888"
print -- "  • 董事长账号: chairman  密码: 888888"
print -- "------------------------------------------------------------------"
print -- "  💡 提示: 保持本终端窗口开启即可维持服务运行。"
print -- "  🛑 如需停止全部服务，可在新终端运行: ${PROJECT_DIR}/stop_all.sh"
print -- "=================================================================="
print -- ""

wait_for_enter
exit 0
