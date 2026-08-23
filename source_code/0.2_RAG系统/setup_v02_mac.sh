#!/bin/bash
set -e
cd "$(dirname "$0")"
./install_postgres_pgvector_mac.sh
./install_ai_models_mac.sh
if [ ! -f .env ]; then cp .env.example .env; fi
echo
echo "基础环境完成。MinerU 尚未安装时再执行： ./install_mineru_mac.sh"
echo "启动： ./run.sh"
