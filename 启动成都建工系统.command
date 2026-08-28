#!/bin/zsh

# ==============================================================================
# 成都建工 V3.0 财税智控与 IDP 穿透中枢 · 桌面一键启动终端
# ==============================================================================
set -u

# 1. 注入标准环境路径，确保双击时能正确找到 uv, node, npx, psql, llama-server 等命令
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"

PROJECT_DIR="/Users/yvoche/AI开发/073_成都建工/V3.0"
START_SCRIPT="${PROJECT_DIR}/start_all.sh"
STOP_SCRIPT="${PROJECT_DIR}/stop_all.sh"
APP_DIR="${PROJECT_DIR}/source_code/0.3_老板端安卓App_天府掌舵"
APP_PID_FILE="${PROJECT_DIR}/.app.pid"
IDP_DIR="${PROJECT_DIR}/source_code/0.4_IDP文档录入引擎_V3.0"
IDP_PID_FILE="${PROJECT_DIR}/.idp.pid"

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
print -- "  🏗️  成都建工 V3.0 财税智控与 IDP 穿透中枢 · 正在启动..."
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

# 3. 启动前强制关掉之前的全部旧服务（确保端口 8921, 8922, 5173, 8930, 8931, 8933 与 PID 完全释放）
print -- "🛑 正在关掉之前的旧服务并清理端口占用..."

# (1) 先执行优雅停止脚本
if [[ -f "$STOP_SCRIPT" ]]; then
  if [[ -x "$STOP_SCRIPT" ]]; then
    "$STOP_SCRIPT" >/dev/null 2>&1 || true
  else
    zsh "$STOP_SCRIPT" >/dev/null 2>&1 || true
  fi
fi

# (2) 强制清理占用 8921(Tax), 8922(RAG), 5173(App), 8930(Ling), 8931(Granite), 8933(IDP) 端口的残留进程
for port in 8921 8922 5173 8930 8931 8933; do
  pids=$(lsof -t -i:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    kill -9 $pids 2>/dev/null || true
  fi
done

# (3) 清理所有 PID 记录文件
rm -f "${PROJECT_DIR}/.tax.pid" "${PROJECT_DIR}/.rag.pid" "${PROJECT_DIR}/.local_llm.pid" "${PROJECT_DIR}/.app.pid" "${PROJECT_DIR}/.idp.pid"
sleep 1
print -- "✅ 之前的服务已全部关闭，端口已释放。"

# 4. 启动后端核心集群 (Tax 8921 + RAG 8922 + PostgreSQL 5432 + Ling LLM 8930)
if [[ ! -f "$START_SCRIPT" ]]; then
  fail_and_wait "找不到启动脚本：${START_SCRIPT}" 1
  exit $?
fi

print -- "🚀 正在拉起后端核心集群 (Tax + RAG + PostgreSQL + Ling LLM)..."
if [[ -x "$START_SCRIPT" ]]; then
  "$START_SCRIPT" --migrate "$@"
else
  zsh "$START_SCRIPT" --migrate "$@"
fi
start_exit=$?

if (( start_exit != 0 )); then
  fail_and_wait "后端核心服务启动异常" "$start_exit"
  exit $start_exit
fi

# 5. 启动 V3.0 IDP 智能文档录入与合规审计引擎 (端口 8933)
if [[ -d "$IDP_DIR" ]]; then
  print -- "📄 正在启动 IDP 智能文档录入与合规审计中枢 (端口 8933)..."
  (
    cd "$IDP_DIR"
    IDP_PORT=8933 nohup "${PROJECT_DIR}/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8933 > /tmp/idp_v3.log 2>&1 &
    echo $! > "$IDP_PID_FILE"
  )
  sleep 1
fi

# 6. 启动老板端移动驾驶舱前端预览 (端口 5173)
if [[ -d "$APP_DIR" ]]; then
  print -- "📱 正在启动老板端移动驾驶舱 (端口 5173)..."
  (
    cd "$APP_DIR" && nohup npx vite preview --port 5173 --host 0.0.0.0 > /tmp/boss_app.log 2>&1 &
    echo $! > "$APP_PID_FILE"
  )
  sleep 2
fi

# 7. 打印完整服务入口看板
print -- ""
print -- "============================================================"
print -- "  🎉 成都建工 V3.0 全系统已就绪！"
print -- "============================================================"
print -- "  【核心访问入口】"
print -- "  📱 老板端移动驾驶舱:    http://127.0.0.1:5173  (推荐)"
print -- "  🏢 税务管理中台:        http://127.0.0.1:8921"
print -- "  🧠 RAG 知识证据库:      http://127.0.0.1:8922"
print -- "  📄 IDP 文档录入与审计:  http://127.0.0.1:8933"
print -- "  🤖 Ling-3.0 办事员接口: http://127.0.0.1:8930/v1"
print -- "  🗄️ PostgreSQL 库:      127.0.0.1:5432 (projectrag)"
print -- "------------------------------------------------------------"
print -- "  💡 保持本窗口开启以维持后台服务运行。"
print -- "  🛑 停止服务："
print -- "     • 可双击桌面【停止成都建工系统.command】"
print -- "     • 或在新终端执行: ${PROJECT_DIR}/stop_all.sh"
print -- "============================================================"
print -- ""

wait_for_enter
exit 0
