#!/bin/zsh
set -u

# scripts/macos 归档入口：自动定位工程根目录，并转交给根目录正式启动器。
resolve_script_dir() {
  local source="$0" base target
  while [[ -L "$source" ]]; do
    base="$(cd -- "$(dirname -- "$source")" && pwd -P)" || return 1
    target="$(readlink "$source")" || return 1
    [[ "$target" = /* ]] && source="$target" || source="${base}/${target}"
  done
  cd -- "$(dirname -- "$source")" && pwd -P
}

SCRIPT_DIR="$(resolve_script_dir)" || exit 1
PROJECT_DIR="${CHENGDU_PROJECT_DIR:-}"

if [[ -z "$PROJECT_DIR" ]]; then
  dir="$SCRIPT_DIR"
  for _ in 1 2 3 4 5 6; do
    if [[ -f "${dir}/启动成都建工系统.command" && -f "${dir}/start_all.sh" ]]; then
      PROJECT_DIR="$dir"
      break
    fi
    parent="$(dirname -- "$dir")"
    [[ "$parent" == "$dir" ]] && break
    dir="$parent"
  done
fi

if [[ -z "$PROJECT_DIR" || ! -f "${PROJECT_DIR}/启动成都建工系统.command" ]]; then
  print -u2 -- "❌ 无法定位成都建工 V3.0 根目录启动器。"
  exit 1
fi

export CHENGDU_PROJECT_DIR="$(cd -- "$PROJECT_DIR" && pwd -P)"
exec zsh "${CHENGDU_PROJECT_DIR}/启动成都建工系统.command" "$@"
