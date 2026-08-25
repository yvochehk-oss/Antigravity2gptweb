#!/bin/zsh

# Double-click entrypoint for the V2.0 stack. Keep the project path independent
# of the location where this launcher is copied.
set -u

PROJECT_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0"
START_SCRIPT="${PROJECT_DIR}/start_all.sh"

wait_for_enter() {
  if [[ -t 0 ]]; then
    read -r "?按回车键关闭此窗口..." || true
  fi
}

fail_and_wait() {
  local message="$1"
  local code="${2:-1}"
  print -u2 -- "启动失败（退出码 ${code}）：${message}"
  wait_for_enter
  return "$code"
}

if [[ ! -d "$PROJECT_DIR" ]]; then
  fail_and_wait "无法定位 V2.0 目录：${PROJECT_DIR}" 1
  exit $?
fi

if ! cd -- "$PROJECT_DIR"; then
  fail_and_wait "无法进入 V2.0 目录：${PROJECT_DIR}" 1
  exit $?
fi

if [[ ! -f "$START_SCRIPT" ]]; then
  fail_and_wait "找不到 V2.0 目录中的 start_all.sh：${START_SCRIPT}" 1
  exit $?
fi

if [[ -x "$START_SCRIPT" ]]; then
  "$START_SCRIPT" "$@"
else
  # Keep the entrypoint usable when its executable bit is lost after copying.
  zsh "$START_SCRIPT" "$@"
fi
start_exit=$?

if (( start_exit != 0 )); then
  fail_and_wait "start_all.sh 执行失败" "$start_exit"
  exit $start_exit
fi

print -- "启动流程已正常结束（退出码 0）。服务地址："
print -- "  Tax：        http://127.0.0.1:8921"
print -- "  RAG：        http://127.0.0.1:8922"
print -- "  local llama： http://127.0.0.1:8930"
wait_for_enter
exit 0
