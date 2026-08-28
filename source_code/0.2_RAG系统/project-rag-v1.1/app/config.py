from pathlib import Path
import os
from dotenv import load_dotenv

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# 强制 HuggingFace / Transformers 离线加载本地模型，杜绝网络超时阻塞
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

DATA_DIR = Path(os.getenv("PROJECT_RAG_DATA_DIR", str(BASE_DIR / "data"))).resolve()
ORIGINAL_DIR = DATA_DIR / "originals"
PARSED_DIR = DATA_DIR / "parsed"
CACHE_DIR = DATA_DIR / "cache"

# V2.0 keeps the imported business originals in a bundle-level directory so
# database paths remain valid when the source checkout is moved as one unit.
# This is a separate read root from DATA_DIR: uploads and parser scratch data
# continue to use the service data directory, while trusted downloads may
# resolve the canonical project_materials paths written by migration 012.
V2_ROOT = BASE_DIR.parents[2]
PROJECT_MATERIALS_DIR = Path(
    os.getenv("PROJECT_RAG_PROJECT_MATERIALS_ROOT", str(V2_ROOT / "project_materials"))
).expanduser().resolve()
try:
    PROJECT_MATERIALS_DIR.relative_to(V2_ROOT.resolve())
except ValueError as exc:
    raise RuntimeError(
        "PROJECT_RAG_PROJECT_MATERIALS_ROOT must remain under the approved V2.0 root"
    ) from exc

# Database
DB_URL = os.getenv("PROJECT_RAG_DB_URL", "").strip()
if not DB_URL:
    raise RuntimeError("PROJECT_RAG_DB_URL is required; ProjectRAG is PostgreSQL-only")
from sqlalchemy.engine import make_url
_backend = make_url(DB_URL).get_backend_name()
if _backend not in {"postgresql", "postgres"}:
    raise RuntimeError(f"ProjectRAG is PostgreSQL-only; unsupported database backend: {_backend}")
IS_POSTGRES = True

# Server
HOST = os.getenv("PROJECT_RAG_HOST", "127.0.0.1")
PORT = int(os.getenv("PROJECT_RAG_PORT", "8922"))

# MinerU settings
LOCAL_MINERU_BIN = BASE_DIR / ".mineru-venv" / "bin" / "mineru"
MINERU_BIN = os.getenv(
    "MINERU_BIN",
    str(LOCAL_MINERU_BIN) if LOCAL_MINERU_BIN.exists() else "mineru"
)
MINERU_BACKEND = os.getenv("MINERU_BACKEND", "").strip()
MINERU_API_URL = os.getenv("MINERU_API_URL", "").strip()

# Embedding settings
EMBEDDING_BACKEND = os.getenv("PROJECT_RAG_EMBEDDING_BACKEND", "bge_m3").strip()
EMBEDDING_MODEL = os.getenv("PROJECT_RAG_EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = int(os.getenv("PROJECT_RAG_EMBEDDING_DIM", "1024"))
if EMBEDDING_DIM != 1024:
    raise RuntimeError("PROJECT_RAG_EMBEDDING_DIM must be 1024; dimension changes require an explicit migration")
EMBEDDING_MAX_LENGTH = int(os.getenv("PROJECT_RAG_EMBEDDING_MAX_LENGTH", "2048"))

# Reranker settings. Disabled by default on low-resource hosts; when disabled,
# app.services.reranker never imports torch/transformers or loads a second model.
RERANKER_ENABLED = os.getenv("PROJECT_RAG_RERANKER_ENABLED", "0").strip().lower() in {
    "1", "true", "yes", "on",
}
RERANKER_BACKEND = os.getenv("PROJECT_RAG_RERANKER_BACKEND", "bge_v2_m3").strip()
RERANKER_MODEL = os.getenv("PROJECT_RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_TOP_N = int(os.getenv("PROJECT_RAG_RERANK_TOP_N", "30"))

# Worker settings
AUTO_START_WORKER = os.getenv("PROJECT_RAG_AUTO_START_WORKER", "1") not in ("0", "false", "False")
WORKER_POLL_SECONDS = float(os.getenv("PROJECT_RAG_WORKER_POLL_SECONDS", "1.0"))
PROCESS_JOBS_INLINE = os.getenv("PROJECT_RAG_PROCESS_JOBS_INLINE", "0") in ("1", "true", "True")
WORKER_SHUTDOWN_TIMEOUT_SECONDS = float(
    os.getenv("PROJECT_RAG_WORKER_SHUTDOWN_TIMEOUT", "10.0")
)
# 并发 Worker 数量（默认 1，pipeline 后端可安全设为 2-4）
WORKER_CONCURRENCY = max(1, int(os.getenv("PROJECT_RAG_WORKER_CONCURRENCY", "1")))

# LLM settings. Ling-3.0-tiny is the V3 local default and is exposed through an
# OpenAI-compatible endpoint; deployments may still override all three values.
LLM_BASE_URL = os.getenv("RAG_LLM_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
LLM_MODEL = os.getenv("RAG_LLM_MODEL", "Ling-3.0-tiny")
LLM_API_KEY = os.getenv("RAG_LLM_API_KEY", "local")

# ``start_all.sh`` injects these values only after its managed llama.cpp
# process has passed the local health check.  They are deliberately separate
# from the user-configured endpoint above: the model pool can therefore keep
# database endpoints (and the legacy environment endpoint) ahead of this
# non-persistent last-resort endpoint without writing a credential or a row to
# the formal database.
LLM_LOCAL_BASE_URL = os.getenv(
    "RAG_LLM_LOCAL_BASE_URL",
    os.getenv("RAG_LOCAL_LLM_BASE_URL", ""),
).rstrip("/")
LLM_LOCAL_MODEL = os.getenv(
    "RAG_LLM_LOCAL_MODEL",
    os.getenv("RAG_LOCAL_LLM_MODEL", ""),
).strip()
try:
    LLM_LOCAL_TIMEOUT_SECONDS = max(
        1,
        min(int(os.getenv("RAG_LLM_LOCAL_TIMEOUT_SECONDS", "60")), 600),
    )
except ValueError:
    # A malformed optional fallback timeout must not prevent the service from
    # starting; the pool still validates the URL/model before using it.
    LLM_LOCAL_TIMEOUT_SECONDS = 60

# Optional external metadata classifier
METADATA_LLM_BASE_URL = os.getenv("RAG_METADATA_LLM_BASE_URL", "").rstrip("/")
METADATA_LLM_MODEL = os.getenv("RAG_METADATA_LLM_MODEL", "")
METADATA_LLM_API_KEY = os.getenv("RAG_METADATA_LLM_API_KEY", "")

# ============================================
# Security settings (NEW in optimized version)
# ============================================

# Allowed base directories for file imports (prevent path traversal)
IMPORT_ROOT = Path(
    os.getenv("PROJECT_RAG_IMPORT_ROOT", str(DATA_DIR / "imports")),
).expanduser().resolve()

# File imports are constrained to the application data directory plus trusted
# V2.0 root and project archives / materials directories.
SAFE_ORIGIN_DIRS = [DATA_DIR.resolve()]
if IMPORT_ROOT not in SAFE_ORIGIN_DIRS:
    SAFE_ORIGIN_DIRS.append(IMPORT_ROOT)
if V2_ROOT.resolve() not in SAFE_ORIGIN_DIRS:
    SAFE_ORIGIN_DIRS.append(V2_ROOT.resolve())
if PROJECT_MATERIALS_DIR not in SAFE_ORIGIN_DIRS:
    SAFE_ORIGIN_DIRS.append(PROJECT_MATERIALS_DIR)
PROJECT_ARCHIVES_DIR = Path(
    os.getenv("PROJECT_RAG_PROJECT_ARCHIVES_ROOT", str(V2_ROOT / "项目存档资料"))
).expanduser().resolve()
if PROJECT_ARCHIVES_DIR.exists() and PROJECT_ARCHIVES_DIR not in SAFE_ORIGIN_DIRS:
    SAFE_ORIGIN_DIRS.append(PROJECT_ARCHIVES_DIR)

# Extra import roots: each path must be explicitly listed here (empty list disables import).
# Paths are resolved and validated at import time.
_ALLOWED_IMPORT_ROOTS_RAW = os.getenv("PROJECT_RAG_ALLOWED_IMPORT_ROOTS", "").strip()
ALLOWED_IMPORT_ROOTS: list[Path] = []
if _ALLOWED_IMPORT_ROOTS_RAW:
    for path_str in _ALLOWED_IMPORT_ROOTS_RAW.split(","):
        path_str = path_str.strip()
        if path_str:
            resolved = Path(path_str).expanduser().resolve()
            if resolved not in SAFE_ORIGIN_DIRS:
                SAFE_ORIGIN_DIRS.append(resolved)
            ALLOWED_IMPORT_ROOTS.append(resolved)

# API authentication is required automatically when the service binds beyond
# loopback.  Production is always fail-closed, including when it binds only
# to loopback.  Local development remains usable without a token unless the
# explicit auth switch or another protected mode is enabled.
APP_ENV = os.getenv("APP_ENV", "").strip().lower()
IS_PRODUCTION = APP_ENV in {"production", "prod"}
# Production-like deployments must never silently run without the shared
# service credential, including common pre-production names used by deployment systems.
PROTECTED_ENVIRONMENTS = frozenset(
    {
        "production",
        "prod",
        "staging",
        "stage",
        "preprod",
        "pre-production",
    }
)
IS_PROTECTED_ENVIRONMENT = APP_ENV in PROTECTED_ENVIRONMENTS
# RAG_SHARED_API_KEY is the only server-side bearer credential.  RAG_API_KEY /
# PROJECT_RAG_API_KEY are retained only as deprecated environment names and are
# deliberately not accepted as authentication credentials.
LEGACY_RAG_API_KEY = os.getenv("RAG_API_KEY", os.getenv("PROJECT_RAG_API_KEY", "")).strip()
RAG_SHARED_API_KEY_FILE = os.getenv("RAG_SHARED_API_KEY_FILE", "").strip()


def _read_shared_api_key_file(raw_path: str) -> str:
    """Read a service credential from an operator-owned file.

    The file mechanism keeps the bearer credential out of ``.env`` and the
    process command line.  It is deliberately strict: a configured file that
    is missing, unreadable, empty, world/group-readable, or contains an
    embedded newline is a configuration error rather than a reason to fall
    back to an unauthenticated service.
    """
    if not raw_path:
        return ""

    secret_path = Path(raw_path).expanduser()
    if not secret_path.is_absolute():
        secret_path = BASE_DIR / secret_path
    try:
        secret_path = secret_path.resolve(strict=True)
        if not secret_path.is_file():
            raise OSError("not a regular file")
        # POSIX permissions are meaningful on macOS/Linux.  Windows ACLs are
        # enforced by the OS and do not map reliably to these mode bits.
        if os.name != "nt" and secret_path.stat().st_mode & 0o077:
            raise PermissionError("file permissions are too broad")
        raw_value = secret_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(
            "RAG_SHARED_API_KEY_FILE is configured but cannot be read securely"
        ) from exc

    value = raw_value.strip(" \t\r\n")
    if not value:
        raise RuntimeError("RAG_SHARED_API_KEY_FILE is configured but empty")
    if "\r" in value or "\n" in value:
        raise RuntimeError(
            "RAG_SHARED_API_KEY_FILE must contain one single-line credential"
        )
    return value


def _resolve_shared_api_key() -> str:
    """Resolve the shared credential, rejecting ambiguous dual sources."""
    env_value = os.getenv("RAG_SHARED_API_KEY", "").strip()
    file_value = _read_shared_api_key_file(RAG_SHARED_API_KEY_FILE)
    if env_value and file_value and env_value != file_value:
        raise RuntimeError(
            "RAG_SHARED_API_KEY and RAG_SHARED_API_KEY_FILE contain different credentials"
        )
    return file_value or env_value


RAG_SHARED_API_KEY = _resolve_shared_api_key()
PROJECT_RAG_AUTH_REQUIRED = os.getenv("PROJECT_RAG_AUTH_REQUIRED", "").strip().lower() in {
    "1", "true", "yes", "on",
}
AUTH_REQUIRED = (
    PROJECT_RAG_AUTH_REQUIRED
    or IS_PROTECTED_ENVIRONMENT
    or bool(RAG_SHARED_API_KEY)
    or HOST not in {"127.0.0.1", "localhost", "::1"}
    or bool(LEGACY_RAG_API_KEY)
)

# A legacy key intentionally triggers fail-closed validation rather than silently
# becoming the server credential; this makes migration mistakes immediately visible.
if AUTH_REQUIRED and not RAG_SHARED_API_KEY:
    raise RuntimeError(
        "RAG_SHARED_API_KEY must be set when API authentication is required "
        "(APP_ENV is a protected production/pre-production value, "
        "PROJECT_RAG_AUTH_REQUIRED=1, non-loopback bind, "
        "or a deprecated RAG_API_KEY/PROJECT_RAG_API_KEY is configured). "
        "Inject the same RAG_SHARED_API_KEY value into both Tax and RAG; "
        "the legacy TAX_RAG_API_KEY cannot satisfy this requirement."
    )

# Maximum upload file size in bytes (default 100MB)
MAX_UPLOAD_SIZE = int(os.getenv("PROJECT_RAG_MAX_UPLOAD_SIZE", "104857600"))

# Rate limiting (requests per minute per IP)
RATE_LIMIT_PER_MINUTE = int(os.getenv("PROJECT_RAG_RATE_LIMIT", "60"))

# Job retry settings
MAX_JOB_RETRIES = int(os.getenv("PROJECT_RAG_MAX_JOB_RETRIES", "3"))
JOB_RETRY_BACKOFF_SECONDS = int(os.getenv("PROJECT_RAG_JOB_RETRY_BACKOFF", "30"))

# BM25 optimization
BM25_CANDIDATE_LIMIT = int(os.getenv("PROJECT_RAG_BM25_LIMIT", "5000"))
BM25_USE_POSTGRES_FTS = os.getenv("PROJECT_RAG_BM25_POSTGRES_FTS", "1") not in ("0", "false", "False")

# Pagination defaults
DEFAULT_PAGE_SIZE = int(os.getenv("PROJECT_RAG_DEFAULT_PAGE_SIZE", "50"))
MAX_PAGE_SIZE = int(os.getenv("PROJECT_RAG_MAX_PAGE_SIZE", "200"))

# Logging
LOG_LEVEL = os.getenv("PROJECT_RAG_LOG_LEVEL", "INFO")

# Stability/observability retention and cache bounds.  These are deliberately
# configuration-only controls: changing them must not alter security policy or
# the canonical facts/calculation boundary.
QUERY_LOG_RETENTION_DAYS = int(
    os.getenv("PROJECT_RAG_QUERY_LOG_RETENTION_DAYS", "30")
)
FACTS_CACHE_TTL_SECONDS = int(
    os.getenv("PROJECT_RAG_FACTS_CACHE_TTL_SECONDS", "60")
)
FACTS_CACHE_MAX_ENTRIES = int(
    os.getenv("PROJECT_RAG_FACTS_CACHE_MAX_ENTRIES", "1024")
)
FACTS_CACHE_EVENT_LIMIT = int(
    os.getenv("PROJECT_RAG_FACTS_CACHE_EVENT_LIMIT", "1000")
)

# ============================================
# V0.3 Retrieval Pipeline
# ============================================

# Query Rewrite (default ON; can be turned off per request)
ENABLE_QUERY_REWRITE = os.getenv("PROJECT_RAG_ENABLE_REWRITE", "1") not in ("0", "false", "False")
REWRITE_LLM_BASE_URL = os.getenv("RAG_REWRITE_LLM_BASE_URL", LLM_BASE_URL).rstrip("/")
REWRITE_LLM_MODEL = os.getenv("RAG_REWRITE_LLM_MODEL", LLM_MODEL)
REWRITE_LLM_API_KEY = os.getenv("RAG_REWRITE_LLM_API_KEY", LLM_API_KEY)
REWRITE_MAX_TOKENS = int(os.getenv("PROJECT_RAG_REWRITE_MAX_TOKENS", "256"))
REWRITE_TEMPERATURE = float(os.getenv("PROJECT_RAG_REWRITE_TEMPERATURE", "0.1"))

# Adaptive HyDE (default ON, but only triggered when Quality Gate says LOW)
ENABLE_HYDE = os.getenv("PROJECT_RAG_ENABLE_HYDE", "1") not in ("0", "false", "False")
HYDE_LLM_BASE_URL = os.getenv("RAG_HYDE_LLM_BASE_URL", LLM_BASE_URL).rstrip("/")
HYDE_LLM_MODEL = os.getenv("RAG_HYDE_LLM_MODEL", LLM_MODEL)
HYDE_LLM_API_KEY = os.getenv("RAG_HYDE_LLM_API_KEY", LLM_API_KEY)
HYDE_MAX_TOKENS = int(os.getenv("PROJECT_RAG_HYDE_MAX_TOKENS", "512"))

# Retrieval Quality Gate thresholds
QUALITY_GATE_TOP1_MIN = float(os.getenv("PROJECT_RAG_GATE_TOP1_MIN", "0.65"))
QUALITY_GATE_AVG_MIN = float(os.getenv("PROJECT_RAG_GATE_AVG_MIN", "0.40"))
QUALITY_GATE_MIN_EVIDENCE = int(os.getenv("PROJECT_RAG_GATE_MIN_EVIDENCE", "2"))

# Retrieval / Fusion
RRF_K = int(os.getenv("PROJECT_RAG_RRF_K", "60"))
CANDIDATE_LIMIT = int(os.getenv("PROJECT_RAG_CANDIDATE_LIMIT", "50"))

# Evidence pack budget
EVIDENCE_MAX_TOKENS = int(os.getenv("PROJECT_RAG_EVIDENCE_MAX_TOKENS", "8192"))
EVIDENCE_PER_QUERY = int(os.getenv("PROJECT_RAG_EVIDENCE_PER_QUERY", "8"))

# Chunk strategy version management
DEFAULT_CHUNK_VERSION = os.getenv("PROJECT_RAG_DEFAULT_CHUNK_VERSION", "v0.3_structured")
LEGACY_CHUNK_VERSION = "v0.2_generic"

# Metadata confidence threshold
METADATA_CONFIDENCE_MIN = float(os.getenv("PROJECT_RAG_META_CONF_MIN", "0.75"))

# Parse quality threshold
PARSE_QUALITY_REVIEW_THRESHOLD = float(os.getenv("PROJECT_RAG_PARSE_QUALITY_REVIEW", "60"))

# Backup settings
BACKUP_DIR = Path(os.getenv("PROJECT_RAG_BACKUP_DIR", str(DATA_DIR / "backups"))).resolve()
BACKUP_KEEP_DAYS = int(os.getenv("PROJECT_RAG_BACKUP_KEEP_DAYS", "30"))
