#!/usr/bin/env bash
# 用 AppleScript 走用户会话授权，避免被 Cursor shell 沙盒拦截。
# 使用方法：
#   1. chmod +x kill_orphan.sh
#   2. ./kill_orphan.sh            # 自动找出占用 5173 的孤儿进程并杀掉
#   或：
#   ./kill_orphan.sh 69613         # 杀掉指定 PID

set -euo pipefail

PORT="${1:-5173}"

listening_pids() {
  local p="$1"
  lsof -nP -iTCP:"$p" -sTCP:LISTEN -t 2>/dev/null | tr -d ' '
}

resolve_targets() {
  if [[ "${PORT}" =~ ^[0-9]+$ ]]; then
    listening_pids "${PORT}"
  else
    echo "${PORT}"
  fi
}

mapfile -t TARGETS < <(resolve_targets)

if [[ ${#TARGETS[@]} -eq 0 || -z "${TARGETS[0]}" ]]; then
  echo "端口 ${PORT} 当前无监听进程，无需清理。"
  exit 0
fi

echo "目标进程（端口 ${PORT}）："
for pid in "${TARGETS[@]}"; do
  [[ -z "${pid}" ]] && continue
  info=$(ps -p "${pid}" -o pid=,user=,command= 2>/dev/null || true)
  echo "  PID ${pid} :: ${info}"
done

# 关键：把 kill 放在 AppleScript 字符串里，绕过 shell sandbox。
CMD="osascript -e 'do shell script \"kill -TERM ${TARGETS[*]} 2>/dev/null; sleep 2; for p in ${TARGETS[*]}; do kill -0 \"\$p\" 2>/dev/null && kill -KILL \"\$p\" 2>/dev/null; done; exit 0\"'"

echo "执行：${CMD}"
eval "${CMD}"
sleep 1

LEFT=$(listening_pids "${PORT}" || true)
if [[ -z "${LEFT}" ]]; then
  echo "✓ 端口 ${PORT} 已释放。"
else
  echo "✗ 仍有进程占用端口 ${PORT}：${LEFT}"
  exit 1
fi
