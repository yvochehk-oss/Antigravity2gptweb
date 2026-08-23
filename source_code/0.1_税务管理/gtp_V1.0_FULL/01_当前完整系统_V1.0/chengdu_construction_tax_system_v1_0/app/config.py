"""V0.2: 税务系统配置。"""
from __future__ import annotations

import os

# ============================================================
# RAG 同步配置
# ============================================================

RAG_SERVICE_URL: str = os.getenv("TAX_RAG_SERVICE_URL", "http://127.0.0.1:8922")
LEGACY_RAG_API_KEY: str = os.getenv("TAX_RAG_API_KEY", "").strip()
AUTO_SYNC_ENABLED: bool = os.getenv("TAX_AUTO_SYNC_ENABLED", "0") not in ("0", "false", "False")

# Cross-system shared secret for Tax ↔ RAG Bearer auth.
#
# ``TAX_RAG_API_KEY`` is a legacy setting and must not be used as an implicit
# fallback.  Keeping the two values separate makes key rotation auditable and
# prevents a stale client-only secret from silently becoming the server's
# authentication credential.
RAG_SHARED_API_KEY: str = os.getenv("RAG_SHARED_API_KEY", "").strip()

_ENVIRONMENT = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
_RAG_AUTH_REQUIRED_FLAG = os.getenv("TAX_RAG_AUTH_REQUIRED", "0").strip().lower()
RAG_AUTH_REQUIRED: bool = (
    _ENVIRONMENT in {"production", "prod", "staging"}
    or AUTO_SYNC_ENABLED
    or bool(LEGACY_RAG_API_KEY)
    or _RAG_AUTH_REQUIRED_FLAG in {"1", "true", "yes", "on"}
)


def validate_rag_auth_config() -> None:
    """Fail closed when the Tax process is configured to call RAG.

    Local development and isolated tests may keep RAG integration disabled and
    therefore do not need a credential just to import the application.  Any
    production/staging, auto-sync, legacy-client-key, or explicitly
    auth-required process must provide a non-blank dedicated shared key.
    """
    if RAG_AUTH_REQUIRED and not RAG_SHARED_API_KEY:
        raise RuntimeError(
            "RAG_SHARED_API_KEY must be provided through the process environment "
            "when Tax→RAG authentication is required; refusing the legacy "
            "TAX_RAG_API_KEY fallback"
        )


validate_rag_auth_config()
SYNC_CONFIDENCE_THRESHOLD: float = float(os.getenv("TAX_SYNC_CONFIDENCE_THRESHOLD", "0.9"))

# ============================================================
# RAG V1.0 Facts Provider 配置
# ============================================================
# Facts Provider 是 RAG V1.0 的统一事实通道，
# 税务系统通过它读取 Analytics Contract 派生指标。
# 默认与 RAG 检索服务共用端口和 key（同一 RAG V1.0 服务）。

RAG_V1_FACTS_URL: str = os.getenv("TAX_RAG_V1_FACTS_URL", RAG_SERVICE_URL).rstrip("/")
RAG_V1_FACTS_API_KEY: str = os.getenv("TAX_RAG_V1_FACTS_API_KEY", RAG_SHARED_API_KEY).strip()
FACTS_DEFAULT_MAX_AGE: int = int(os.getenv("TAX_FACTS_MAX_AGE", "60"))
FACTS_REQUIRE_FRESH_FOR_AI: bool = os.getenv("TAX_FACTS_REQUIRE_FRESH", "1") not in ("0", "false", "False")
