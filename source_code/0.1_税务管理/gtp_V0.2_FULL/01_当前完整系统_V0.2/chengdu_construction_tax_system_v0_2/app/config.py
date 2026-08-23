"""V0.2: 税务系统配置。"""
from __future__ import annotations

import os

# ============================================================
# RAG 同步配置
# ============================================================

RAG_SERVICE_URL: str = os.getenv("TAX_RAG_SERVICE_URL", "http://127.0.0.1:8800")
RAG_API_KEY: str = os.getenv("TAX_RAG_API_KEY", "")
AUTO_SYNC_ENABLED: bool = os.getenv("TAX_AUTO_SYNC_ENABLED", "0") not in ("0", "false", "False")
SYNC_CONFIDENCE_THRESHOLD: float = float(os.getenv("TAX_SYNC_CONFIDENCE_THRESHOLD", "0.9"))

# ============================================================
# RAG V1.0 Facts Provider 配置
# ============================================================
# Facts Provider 是 RAG V1.0 的统一事实通道，
# 税务系统通过它读取 Analytics Contract 派生指标。
# 默认与 RAG 检索服务共用端口和 key（同一 RAG V1.0 服务）。

RAG_V1_FACTS_URL: str = os.getenv("TAX_RAG_V1_FACTS_URL", RAG_SERVICE_URL).rstrip("/")
RAG_V1_FACTS_API_KEY: str = os.getenv("TAX_RAG_V1_FACTS_API_KEY", RAG_API_KEY)
FACTS_DEFAULT_MAX_AGE: int = int(os.getenv("TAX_FACTS_MAX_AGE", "60"))
FACTS_REQUIRE_FRESH_FOR_AI: bool = os.getenv("TAX_FACTS_REQUIRE_FRESH", "1") not in ("0", "false", "False")
