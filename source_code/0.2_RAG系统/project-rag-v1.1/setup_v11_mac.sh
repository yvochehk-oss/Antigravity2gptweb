#!/bin/bash
# ProjectRAG V1.1 - Setup Script
set -e
cd "$(dirname "$0")"
./install_postgres_pgvector_mac.sh
./install_ai_models_mac.sh
if [ ! -f .env ]; then cp .env.example .env; fi
echo
echo "基础环境完成。MinerU 尚未安装时再执行： ./install_mineru_mac.sh"
echo "启动： ./run.sh"
echo
echo "V1.1 额外说明："
echo "  - 法规数据导入: python -m app.services.regulations_md_ingest --dry"
echo "  - ai_review 表自动建表（启动后查看 facts_snapshots / ai_review_runs）"