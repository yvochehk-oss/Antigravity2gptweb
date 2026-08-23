"""Configuration settings for ProjectRAG V0.2 Optimized.

All configuration values are loaded from environment variables with sensible defaults.
"""
from pathlib import Path
import os

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PROJECT_RAG_DATA_DIR", str(BASE_DIR / "data"))).resolve()
ORIGINAL_DIR = DATA_DIR / "originals"
PARSED_DIR = DATA_DIR / "parsed"
CACHE_DIR = DATA_DIR / "cache"

# Database
DB_URL = os.getenv(
    "PROJECT_RAG_DB_URL",
    "postgresql+psycopg://projectrag:projectrag@127.0.0.1:5432/projectrag"
)
IS_POSTGRES = DB_URL.startswith("postgresql")

# Server
HOST = os.getenv("PROJECT_RAG_HOST", "127.0.0.1")
PORT = int(os.getenv("PROJECT_RAG_PORT", "8800"))

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
EMBEDDING_MAX_LENGTH = int(os.getenv("PROJECT_RAG_EMBEDDING_MAX_LENGTH", "2048"))

# Reranker settings
RERANKER_BACKEND = os.getenv("PROJECT_RAG_RERANKER_BACKEND", "bge_v2_m3").strip()
RERANKER_MODEL = os.getenv("PROJECT_RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_TOP_N = int(os.getenv("PROJECT_RAG_RERANK_TOP_N", "30"))

# Worker settings
AUTO_START_WORKER = os.getenv("PROJECT_RAG_AUTO_START_WORKER", "1") not in ("0", "false", "False")
WORKER_POLL_SECONDS = float(os.getenv("PROJECT_RAG_WORKER_POLL_SECONDS", "1.0"))
PROCESS_JOBS_INLINE = os.getenv("PROJECT_RAG_PROCESS_JOBS_INLINE", "0") in ("1", "true", "True")

# LLM settings
LLM_BASE_URL = os.getenv("RAG_LLM_BASE_URL", "").rstrip("/")
LLM_MODEL = os.getenv("RAG_LLM_MODEL", "")
LLM_API_KEY = os.getenv("RAG_LLM_API_KEY", "")

# Optional external metadata classifier
METADATA_LLM_BASE_URL = os.getenv("RAG_METADATA_LLM_BASE_URL", "").rstrip("/")
METADATA_LLM_MODEL = os.getenv("RAG_METADATA_LLM_MODEL", "")
METADATA_LLM_API_KEY = os.getenv("RAG_METADATA_LLM_API_KEY", "")

# ============================================
# Security settings (NEW in optimized version)
# ============================================

# Allowed base directories for file imports (prevent path traversal)
SAFE_ORIGIN_DIRS = [
    DATA_DIR.resolve(),
    Path(os.path.expanduser("~")).resolve(),
]

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
