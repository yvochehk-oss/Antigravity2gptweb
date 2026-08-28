#!/usr/bin/env bash
# 一键生成 Windows 干净分发包（排除 Mac 专用的 .venv, node_modules 与缓存）
set -e

SOURCE_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0"
TARGET_ZIP="/Users/yvoche/AI开发/073_成都建工_V2.0_Windows部署包.zip"

echo "=== 正在打包 Windows 极速部署包 ==="
cd "$SOURCE_DIR"

zip -r -q "$TARGET_ZIP" . \
  -x "*.venv/*" \
  -x "*node_modules/*" \
  -x "*.git/*" \
  -x "*__pycache__/*" \
  -x "*.pytest_cache/*" \
  -x "*.ruff_cache/*" \
  -x "*.log" \
  -x "*.DS_Store" \
  -x "*.pid"

echo "=== 打包完成！==="
echo "压缩包位置: $TARGET_ZIP"
ls -lh "$TARGET_ZIP"
