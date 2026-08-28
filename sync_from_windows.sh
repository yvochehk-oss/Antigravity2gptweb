#!/usr/bin/env bash
# ==============================================================================
# 成都建工 V2.0 - macOS 本地同步脚本
# main / macos / windows 为同一主体代码的三个镜像引用，不再互相做冲突合并。
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

echo ">>> [1/4] 检查本地工作区..."
if [[ -n "$(git status --porcelain)" ]]; then
  echo "[ERROR] 工作区存在未提交修改。为避免覆盖本地工作，请先提交或暂存后再同步。" >&2
  exit 1
fi

echo ">>> [2/4] 拉取 main / macos / windows 镜像..."
git fetch origin main macos windows --prune

MAIN_SHA="$(git rev-parse origin/main)"
MACOS_SHA="$(git rev-parse origin/macos)"
WINDOWS_SHA="$(git rev-parse origin/windows)"
if [[ "$MAIN_SHA" != "$MACOS_SHA" || "$MAIN_SHA" != "$WINDOWS_SHA" ]]; then
  echo "[ERROR] 远程三个主体分支尚未镜像到同一提交，停止本地同步。" >&2
  printf 'main=%s\nmacos=%s\nwindows=%s\n' "$MAIN_SHA" "$MACOS_SHA" "$WINDOWS_SHA" >&2
  echo "请先检查 GitHub 的 Canonical Branch Mirror 工作流，禁止本地自动解冲突。" >&2
  exit 2
fi

echo ">>> [3/4] 快进本地 macOS 分支到统一主体代码..."
if git show-ref --verify --quiet refs/heads/macos; then
  git switch macos
else
  git switch --track -c macos origin/macos
fi
git merge --ff-only origin/macos

echo ">>> [4/4] 重新构建并同步 Tax 前端静态资源..."
FRONTEND_DIR="source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/frontend_stitch"
if [[ -d "$FRONTEND_DIR" ]]; then
  (
    cd "$FRONTEND_DIR"
    npm run sync:tax-static
  )
fi

echo "=================================================================="
echo "✓ macOS 已同步到统一主体提交：$MAIN_SHA"
echo "✓ main / macos / windows 远程代码一致。"
echo "=================================================================="
