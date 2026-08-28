#!/usr/bin/env bash
# 一键生成 Windows 极速精简部署包（纯源码+数据库备份+脚本+Windows运行时，体积 < 35MB，秒级传输）
set -e

SOURCE_DIR="/Users/yvoche/AI开发/073_成都建工/V2.0"
TARGET_ZIP="/Users/yvoche/AI开发/073_成都建工_V2.0_Windows精简部署包.zip"

echo "=== 正在打包 Windows 极速精简部署包 (<35MB) ==="
cd "$SOURCE_DIR"

if [ -f "$TARGET_ZIP" ]; then
    rm -f "$TARGET_ZIP"
fi

zip -r "$TARGET_ZIP" \
  database/ \
  project_materials/ \
  scripts/ \
  source_code/ \
  windows_scripts/ \
  一键启动_Windows.bat \
  projectrag_backup_full_20260827.dump \
  WINDOWS_DEPLOY_GUIDE.md \
  models/local-llm/runtime-win-cpu-x64/ \
  -x "*venv*" \
  -x "*venv*/*" \
  -x "*node_modules*" \
  -x "*node_modules*/*" \
  -x "*/data/parsed/*" \
  -x "*/data/parsed" \
  -x "source_code/0.2_RAG系统/project-rag-v1.1/data/parsed/*" \
  -x "*.git*" \
  -x "*.git*/*" \
  -x "*__pycache__*" \
  -x "*__pycache__*/*" \
  -x "*.pytest_cache*" \
  -x "*.pytest_cache*/*" \
  -x "*.ruff_cache*" \
  -x "*.ruff_cache*/*" \
  -x "*.log" \
  -x "*.DS_Store" \
  -x "*.pid"

echo "=== 精简打包完成！==="
echo "压缩包位置: $TARGET_ZIP"
ls -lh "$TARGET_ZIP"
