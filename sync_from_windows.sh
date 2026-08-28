#!/bin/bash
# ==============================================================================
# 成都建工 V2.0 - macOS 本地一键从 Windows 分支同步最新功能与样式
# ==============================================================================
set -e

echo ">>> [1/3] 正在拉取远程最新分支与提交..."
git fetch origin windows

echo ">>> [2/3] 正在将 Windows 分支的功能、前端排版、字体与样式合并到 macOS 分支..."
git merge origin/windows --no-edit -m "sync: 从 windows 分支同步最新功能、UI排版与样式规范" || {
  echo ">>> 检测到平台特异性差异，自动优先采纳跨平台源码并保留本地 macOS 启动脚本..."
  git checkout --theirs source_code/ || true
  git checkout --ours start_all.sh stop_all.sh || true
  git add -A
  git commit -m "sync: 自动完成跨平台功能与排版合并（保留 macOS 启动配置）" || true
}

echo ">>> [3/3] 正在重新编译前端以生效最新排版与样式..."
if [ -d "source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/frontend_stitch" ]; then
  (cd "source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/frontend_stitch" && npm run build && npm run sync:tax-static)
fi

echo "=================================================================="
echo "✓ Windows 分支最新功能、排版、字体与样式已成功同步至 macOS 分支！"
echo "=================================================================="
