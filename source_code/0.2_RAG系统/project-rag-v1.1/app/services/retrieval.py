"""Optimized retrieval service with BM25 and vector fusion.

Provides hybrid search combining lexical (BM25) and semantic (vector) retrieval.
Includes V0.3 Adaptive Retrieval Pipeline with Query Rewrite, Quality Gate, and HyDE.
"""
import json
import math
import re
import time
from collections import Counter
from dataclasses import asdict, is_dataclass
from typing import Optional, Any
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import Chunk, Document, QueryLog
from ..domain.entities import (
    CANONICAL_ENTITY_CODES,
    CANONICAL_ENTITY_RANGE_TEXT,
    is_canonical_entity_code,
    normalize_entity_code,
)
from ..config import (
    IS_POSTGRES, EMBEDDING_DIM, EMBEDDING_BACKEND,
    RERANKER_BACKEND, RERANK_TOP_N, BM25_CANDIDATE_LIMIT, BM25_USE_POSTGRES_FTS,
    ENABLE_QUERY_REWRITE, ENABLE_HYDE,
    RRF_K, CANDIDATE_LIMIT as CFG_CANDIDATE_LIMIT,
    DEFAULT_CHUNK_VERSION, LEGACY_CHUNK_VERSION
)
from ..logging_config import get_logger
from .embeddings import embed, cosine
from .reranker import rerank

logger = get_logger(__name__)

# Lazy import for V0.3 modules (may not exist yet)
_query_rewrite_module = None
_quality_gate_module = None


def _get_query_rewrite_module():
    global _query_rewrite_module
    if _query_rewrite_module is None:
        try:
            from . import query_rewrite
            _query_rewrite_module = query_rewrite
        except ImportError:
            _query_rewrite_module = None
    return _query_rewrite_module


def _get_quality_gate_module():
    global _quality_gate_module
    if _quality_gate_module is None:
        try:
            from . import quality_gate
            _quality_gate_module = quality_gate
        except ImportError:
            _quality_gate_module = None
    return _quality_gate_module


# Tokenizer regex for Chinese characters and English words
TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")

# A business role is an aggregation dimension.  These maps let retrieval
# constrain results by role while keeping the SQL identity column strictly
# canonical entity_code (A01-A11/B01-B10/C01-C02/D01-D03).  Role labels are never rewritten into
# virtual company identifiers.
BUSINESS_ROLE_ENTITY_CODES = {
    "A": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("A"))),
    "B": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("B"))),
    "C": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("C"))),
    "D": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("D"))),
    "a": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("A"))),
    "b": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("B"))),
    "c": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("C"))),
    "d": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("D"))),
    "construction": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("A"))),
    "trade": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("B"))),
    "labor": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("C"))),
    "equipment": tuple(sorted(code for code in CANONICAL_ENTITY_CODES if code.startswith("D"))),
}


def _as_dict(value: object) -> dict | None:
    """Convert a pipeline dataclass/dict to a plain serialisable mapping."""
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return {
            key: item for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return None


def _non_empty_filter(value: object) -> bool:
    return value is not None and value != "" and value != []


def _normalise_filter_values(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item).strip() for item in values if _non_empty_filter(item)]


def _validate_filters(filters: dict | None) -> dict:
    """Validate and normalize retrieval filters before building SQL.

    Entity filters are security/data-integrity boundaries.  Invalid values
    (including A/B/C/D and 甲乙丙丁) are rejected rather than silently
    returning an unscoped result.  ``business_role`` is explicitly mapped to
    canonical codes by :func:`_base_stmt`.
    """
    if filters is None:
        return {}
    if not isinstance(filters, dict):
        raise ValueError("retrieval filters must be an object")

    allowed = {
        "document_type",
        "entity_code",
        "counterparty_code",
        "business_category",
        "business_role",
        "tax_category",
        "period",
        "confidentiality",
        "version_status",
    }
    unknown = sorted(set(filters) - allowed)
    if unknown:
        raise ValueError(f"unsupported retrieval filter(s): {', '.join(unknown)}")

    normalized = dict(filters)
    if _non_empty_filter(filters.get("entity_code")):
        codes = []
        for raw in _normalise_filter_values(filters["entity_code"]):
            code = normalize_entity_code(raw)
            if not is_canonical_entity_code(code):
                raise ValueError(
                    f"invalid entity_code filter {raw!r}; expected canonical {CANONICAL_ENTITY_RANGE_TEXT}"
                )
            codes.append(code)
        normalized["entity_code"] = codes

    if _non_empty_filter(filters.get("counterparty_code")):
        counterparties = _normalise_filter_values(filters["counterparty_code"])
        virtual = {"甲", "乙", "丙", "丁", "A", "B", "C", "D"}
        if any(item.upper() in virtual for item in counterparties):
            raise ValueError("virtual placeholder is not a counterparty filter")
        normalized["counterparty_code"] = counterparties

    if _non_empty_filter(filters.get("business_role")):
        roles = [item.lower() for item in _normalise_filter_values(filters["business_role"])]
        unknown_roles = [role for role in roles if role not in BUSINESS_ROLE_ENTITY_CODES]
        if unknown_roles:
            raise ValueError(f"unsupported business_role filter(s): {', '.join(unknown_roles)}")
        normalized["business_role"] = roles

    return normalized


def _business_role_for_entity(entity_code: str | None) -> str:
    """Return the descriptive business role for a canonical code."""
    code = normalize_entity_code(entity_code)
    if not code:
        return ""
    return {
        "A": "construction",
        "B": "trade",
        "C": "labor",
        "D": "equipment",
    }.get(code[:1], "")


def _estimate_tokens(text: str) -> int:
    """Estimate token count (simplified: CJK chars = 1 token, others = 4 chars/token)."""
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return cjk + max(0, len(text) - cjk) // 4


def tokens(text: str) -> list[str]:
    """Extract tokens from text.

    Args:
        text: Input text

    Returns:
        List of lowercase tokens
    """
    return TOKEN_RE.findall((text or "").lower())


def _bm25(query_tokens: list[str], docs_tokens: list[list[str]]) -> list[float]:
    """Calculate BM25 scores for documents.

    Args:
        query_tokens: Query token list
        docs_tokens: List of document token lists

    Returns:
        List of BM25 scores
    """
    n = len(docs_tokens) or 1
    avg = sum(len(x) for x in docs_tokens) / n or 1

    df = Counter()
    for ds in docs_tokens:
        for t in set(ds):
            df[t] += 1

    scores = []
    k1, b = 1.5, 0.75

    for ds in docs_tokens:
        tf = Counter(ds)
        dl = len(ds)
        score = 0.0

        for q in query_tokens:
            if not tf[q]:
                continue
            idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
            score += idf * (tf[q] * (k1 + 1)) / (tf[q] + k1 * (1 - b + b * dl / avg))

        scores.append(score)

    mx = max(scores) if scores else 0
    return [s / mx if mx else 0 for s in scores]


def _pg_fts_search(db: Session, base_stmt, query: str, limit: int) -> list[tuple]:
    """Use PostgreSQL full-text search for BM25.

    Args:
        db: Database session
        base_stmt: Base query statement
        query: Search query
        limit: Maximum results

    Returns:
        List of (Chunk, Document) tuples
    """
    try:
        query_tokens = tokens(query)
        if not query_tokens:
            return []
        ts_query = " & ".join(query_tokens)
        clause = text("to_tsvector('simple', chunks.search_text) @@ to_tsquery('simple', :tsq)").bindparams(tsq=ts_query)
        order_clause = text("ts_rank(to_tsvector('simple', chunks.search_text), to_tsquery('simple', :tsq)) DESC").bindparams(tsq=ts_query)
        stmt = base_stmt.where(clause).order_by(order_clause).limit(limit)

        return db.execute(stmt).all()
    except Exception as e:
        logger.warning(f"PostgreSQL FTS fallback to Python BM25: {e}")
        return []


def _base_stmt(project_id: int, filters: dict):
    """Build base query statement with filters.

    Args:
        project_id: Project ID
        filters: Metadata filters

    Returns:
        SQLAlchemy select statement
    """
    filters = _validate_filters(filters)
    stmt = select(Chunk, Document).join(
        Document, Chunk.document_id == Document.id
    ).where(
        Chunk.project_id == project_id,
        (Document.version_status.in_(["effective", "ACTIVE", "active"]) | Document.version_status.is_(None) | (Document.version_status == "")),
        (Document.duplicate_of_id.is_(None) | (Document.duplicate_of_id == 0))
    )

    field_map = {
        "document_type": Document.document_type,
        "entity_code": Document.entity_code,
        "counterparty_code": Document.counterparty_code,
        "business_category": Document.business_category,
        "tax_category": Document.tax_category,
        "period": Document.period,
        "confidentiality": Document.confidentiality,
        "version_status": Document.version_status,
    }

    for key, col in field_map.items():
        val = filters.get(key)
        if val:
            vals = val if isinstance(val, list) else [val]
            stmt = stmt.where(col.in_([str(x) for x in vals]))

    # ``business_role`` is not a company identity and is not stored as a
    # virtual entity column on documents.  Resolve it to the real canonical
    # code set at the query boundary.  This also works for legacy documents
    # whose only persisted dimension is entity_code.
    roles = filters.get("business_role")
    if roles:
        role_values = roles if isinstance(roles, list) else [roles]
        role_codes = sorted({
            code
            for role in role_values
            for code in BUSINESS_ROLE_ENTITY_CODES[str(role).lower()]
        })
        stmt = stmt.where(Document.entity_code.in_(role_codes))

    return stmt


def _hybrid_search(
    db: Session,
    project_id: int,
    query: str,
    filters: dict,
    top_k: int,
    candidate_limit: int = None,
    diagnostics: dict | None = None,
) -> list[dict]:
    """Perform hybrid search combining vector and BM25 with RRF fusion.

    This is an internal function that performs the core hybrid search without reranking.
    Returns list of dicts with 'score' field (not 'rerank_score').

    Args:
        db: Database session
        project_id: Project to search
        query: Search query
        filters: Metadata filters
        top_k: Number of results to return
        candidate_limit: Override candidate limit (default from config)

    Returns:
        List of result dictionaries with score, chunk, and document info
    """
    if candidate_limit is None:
        candidate_limit = min(BM25_CANDIDATE_LIMIT, max(RERANK_TOP_N, top_k * 3))

    diagnostics = diagnostics if diagnostics is not None else {}
    qvec = embed(query)
    qtokens = tokens(query)
    base = _base_stmt(project_id, filters)

    row_map = {}  # chunk_id -> (chunk, document)
    score_map = {}  # chunk_id -> fused score
    vector_scores = {}  # chunk_id -> raw vector score
    bm25_scores = {}  # chunk_id -> raw BM25 score

    # === Vector Search (PostgreSQL pgvector) ===
    if IS_POSTGRES and len(qvec) != EMBEDDING_DIM:
        diagnostics["vector_status"] = "FAILED_DIMENSION"
        diagnostics["vector_error"] = (
            f"query embedding dimension {len(qvec)} != configured {EMBEDDING_DIM}"
        )
        logger.error(diagnostics["vector_error"])
    elif IS_POSTGRES and len(qvec) == EMBEDDING_DIM:
        try:
            vstmt = base.where(
                Chunk.embedding.is_not(None)
            ).order_by(
                Chunk.embedding.op("<=>")(qvec)
            ).limit(candidate_limit)

            vrows = db.execute(vstmt).all()

            for rank, (c, d) in enumerate(vrows, 1):
                row_map[c.id] = (c, d)
                rr_score = 1.0 / (RRF_K + rank)
                score_map[c.id] = rr_score
                vector_scores[c.id] = 1.0 / rank  # Raw vector rank score

            logger.debug(f"Vector search returned {len(vrows)} candidates")
            diagnostics["vector_status"] = "OK"

        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            diagnostics["vector_status"] = "FAILED"
            diagnostics["vector_error"] = str(e)
    else:
        diagnostics["vector_status"] = "LOCAL"

    # === Lexical Search (BM25) ===
    use_python_bm25 = not IS_POSTGRES or not BM25_USE_POSTGRES_FTS
    lexical_rows = _pg_fts_search(db, base, query, candidate_limit)

    if not lexical_rows:
        # The Python BM25 fallback must score the complete filtered corpus so
        # a match after the historical first-5000 cap cannot disappear.  The
        # candidate limit still bounds the ranked output below; it must not
        # bound the input corpus used for lexical recall/IDF.
        lexical_rows = db.execute(base).all()
        use_python_bm25 = True
        diagnostics["lexical_backend"] = "python_bm25"
    else:
        diagnostics["lexical_backend"] = "postgres_fts"

    if lexical_rows:
        if use_python_bm25:
            dtokens = [tokens(c.search_text) for c, d in lexical_rows]
            bm_scores = _bm25(qtokens, dtokens)

            lexical_rank = sorted(
                range(len(lexical_rows)),
                key=lambda i: bm_scores[i],
                reverse=True
            )[:candidate_limit]
        else:
            lexical_rank = list(range(len(lexical_rows)))[:candidate_limit]

        for rank, idx in enumerate(lexical_rank, 1):
            if idx >= len(lexical_rows):
                continue
            c, d = lexical_rows[idx]
            row_map[c.id] = (c, d)
            rr_score = 1.0 / (RRF_K + rank)
            score_map[c.id] = score_map.get(c.id, 0) + rr_score
            bm25_scores[c.id] = 1.0 / rank  # Raw BM25 rank score

    # === Fallback for non-PostgreSQL ===
    if not IS_POSTGRES:
        for i, (c, d) in enumerate(lexical_rows):
            try:
                vec = json.loads(c.embedding_json or "[]")
            except (json.JSONDecodeError, TypeError):
                vec = []
            vs = max(0.0, cosine(qvec, vec))
            if vs > 0:
                score_map[c.id] = score_map.get(c.id, 0) + 0.5 * vs
                vector_scores[c.id] = vs
            row_map[c.id] = (c, d)

    # === Build final candidate list (sorted by fused score) ===
    candidates = []
    for cid, score in sorted(score_map.items(), key=lambda kv: kv[1], reverse=True)[:candidate_limit]:
        if cid not in row_map:
            continue
        c, d = row_map[cid]
        candidates.append({
            "score": round(float(score), 6),
            "vector_score": round(float(vector_scores.get(cid, 0.0)), 6),
            "bm25_score": round(float(bm25_scores.get(cid, 0.0)), 6),
            "chunk_id": c.id,
            "document_id": d.id,
            "document_code": d.document_code,
            "filename": d.filename,
            "document_type": d.document_type,
            "entity_code": d.entity_code,
            "business_role": _business_role_for_entity(d.entity_code),
            "counterparty_code": d.counterparty_code,
            "business_category": d.business_category,
            "period": d.period,
            "heading_path": c.heading_path,
            "title_chain": getattr(c, 'title_chain', ''),
            "page_start": c.page_start,
            "page_end": c.page_end,
            "content_type": c.content_type,
            "text": c.content
        })

    return candidates


def _merge_with_rrf(
    list_a: list[dict],
    list_b: list[dict],
    k: int = None
) -> list[dict]:
    """Merge two result lists using Reciprocal Rank Fusion (RRF).

    Args:
        list_a: First result list (e.g., original search results)
        list_b: Second result list (e.g., HyDE results)
        k: RRF k parameter (default from config)

    Returns:
        Merged and re-ranked list
    """
    if k is None:
        k = RRF_K

    if not list_a:
        return list_b
    if not list_b:
        return list_a

    # Build score map: chunk_id -> RRF fused score
    fused_scores = {}

    # Add list_a scores (normalize by position)
    for idx, item in enumerate(list_a):
        cid = item.get("chunk_id")
        if cid is not None:
            rr_score = 1.0 / (k + idx + 1)
            fused_scores[cid] = fused_scores.get(cid, 0) + rr_score

    # Add list_b scores
    for idx, item in enumerate(list_b):
        cid = item.get("chunk_id")
        if cid is not None:
            rr_score = 1.0 / (k + idx + 1)
            fused_scores[cid] = fused_scores.get(cid, 0) + rr_score

    # Merge items preserving all fields
    all_items = {item.get("chunk_id"): item for item in list_a + list_b}

    # Sort by fused score
    merged = [
        all_items[cid]
        for cid, _ in sorted(fused_scores.items(), key=lambda kv: kv[1], reverse=True)
        if cid in all_items
    ]

    return merged


def _attach_explain_metadata(
    results: list[dict],
    query_tokens: list[str]
) -> list[dict]:
    """Attach explanation metadata to search results.

    Adds matched_keywords, vector_score, bm25_score fields to each result.

    Args:
        results: Search results from _hybrid_search
        query_tokens: Tokenized query for keyword matching

    Returns:
        Results with explanation metadata attached
    """
    if not results:
        return results

    # Normalize query tokens to lowercase set
    query_set = set(t.lower() for t in query_tokens)

    enriched = []
    for item in results:
        # Make a copy to avoid mutating original
        enriched_item = dict(item)

        # Extract matched keywords (case-insensitive)
        text_lower = (item.get("text", "") or "").lower()
        matched = [
            token for token in query_set
            if token in text_lower
        ]
        enriched_item["matched_keywords"] = matched

        # Ensure score fields exist (may have been stripped by reranker)
        enriched_item["vector_score"] = item.get("vector_score", 0.0)
        enriched_item["bm25_score"] = item.get("bm25_score", 0.0)

        # Add metadata match info
        enriched_item["metadata_matched"] = {
            "filename_match": any(t in (item.get("filename") or "").lower() for t in query_set),
            "heading_match": any(t in (item.get("heading_path") or "").lower() for t in query_set),
        }

        enriched.append(enriched_item)

    return enriched


def retrieve(
    db: Session,
    project_id: int,
    query: str,
    filters: dict,
    top_k: int = 10,
    use_rerank: bool = True,
    rewrite: bool = False,
    hyde: bool = False,
    deep: bool = False
) -> list[dict] | dict:
    """Perform hybrid retrieval with optional adaptive pipeline.

    V0.3 Adaptive Retrieval Pipeline:
    1. Query Rewrite (optional)
    2. Hybrid Search (vector + BM25)
    3. Reranking
    4. Quality Gate assessment
    5. Adaptive HyDE (if quality is LOW)

    Args:
        db: Database session
        project_id: Project to search
        query: Search query
        filters: Metadata filters
        top_k: Number of results to return
        use_rerank: Whether to use reranking
        rewrite: Enable query rewrite
        hyde: Enable HyDE generation
        deep: Enable full deep retrieval mode

    Returns:
        - When rewrite=False, hyde=False, deep=False: list[dict] (backward compatible)
        - Otherwise: dict with full pipeline metadata
    """
    start_time = time.time()
    adaptive_requested = bool(rewrite or hyde or deep)

    original_query = query
    original_filters = _validate_filters(filters)
    effective_filters = dict(original_filters)
    rewrite_result = None
    hyde_result = None
    hyde_used = False
    quality_gate_result = None
    pipeline_errors: list[dict] = []
    bm25_candidates: list[int] = []
    vector_candidates: list[int] = []
    search_diagnostics: dict = {}
    reranker_status = "SKIPPED" if not use_rerank else "OK"

    def _record_error(step: str, error: object, *, code: str = "STEP_FAILED") -> None:
        message = str(error)
        pipeline_errors.append({"step": step, "code": code, "message": message})
        logger.error("Retrieval pipeline step %s failed: %s", step, message)

    # Step 1: Query Rewrite.  The helper contract is
    # rewrite_query(query, filters, project_meta=None); do not pass a DB
    # session/project id as positional arguments.  Dataclass results are
    # converted explicitly so they can be logged and returned as JSON.
    if adaptive_requested and (rewrite or deep):
        qr_mod = _get_query_rewrite_module()
        if not ENABLE_QUERY_REWRITE:
            rewrite_result = {
                "intent": "其他",
                "rewritten_query": original_query,
                "filters": dict(effective_filters),
                "keywords": [],
                "confidence": 0.0,
                "status": "DISABLED",
                "error": "query rewrite disabled by configuration",
            }
        elif not qr_mod or not hasattr(qr_mod, "rewrite_query"):
            rewrite_result = {
                "intent": "其他",
                "rewritten_query": original_query,
                "filters": dict(effective_filters),
                "keywords": [],
                "confidence": 0.0,
                "status": "UNAVAILABLE",
                "error": "query rewrite service is unavailable",
            }
            _record_error("query_rewrite", rewrite_result["error"], code="UNAVAILABLE")
        else:
            try:
                raw_rewrite = qr_mod.rewrite_query(
                    original_query,
                    {
                        key: list(value) if isinstance(value, list) else value
                        for key, value in effective_filters.items()
                    },
                    project_meta=None,
                )
                rewrite_result = _as_dict(raw_rewrite)
                if not rewrite_result:
                    raise TypeError("query rewrite returned no structured result")

                proposed_filters = rewrite_result.get("filters") or {}
                try:
                    safe_proposed = _validate_filters(proposed_filters)
                except ValueError as exc:
                    _record_error("query_rewrite", exc, code="INVALID_FILTER")
                    safe_proposed = {}

                # Explicit request filters remain authoritative.  A rewrite
                # can add a role/category constraint but cannot broaden or
                # replace a canonical entity_code selected by the caller.
                for key, value in safe_proposed.items():
                    if key not in effective_filters or not _non_empty_filter(effective_filters[key]):
                        effective_filters[key] = value
                rewrite_result["filters"] = dict(effective_filters)

                status = str(rewrite_result.get("status") or "OK")
                error = rewrite_result.get("error")
                if status not in {"OK", "DISABLED"}:
                    _record_error("query_rewrite", error or status, code=status)
                rewritten = rewrite_result.get("rewritten_query")
                if status == "OK" and isinstance(rewritten, str) and rewritten.strip():
                    query = rewritten.strip()
                    logger.info(
                        "Query rewritten: '%s...' -> '%s...'",
                        original_query[:30], query[:30],
                    )
                else:
                    query = original_query
            except Exception as exc:
                # Keep the original query only as an explicitly reported
                # degraded step.  The caller can distinguish this from a
                # successful rewrite by status/pipeline_errors.
                query = original_query
                rewrite_result = {
                    "intent": "其他",
                    "rewritten_query": original_query,
                    "filters": dict(effective_filters),
                    "keywords": [],
                    "confidence": 0.0,
                    "status": "ERROR",
                    "error": str(exc),
                }
                _record_error("query_rewrite", exc)

    # Step 2: Hybrid Search
    candidates = _hybrid_search(
        db,
        project_id,
        query,
        effective_filters,
        top_k,
        diagnostics=search_diagnostics,
    )
    if search_diagnostics.get("vector_status", "").startswith("FAILED"):
        _record_error(
            "vector_search",
            search_diagnostics.get("vector_error", "vector search failed"),
            code=search_diagnostics["vector_status"],
        )
    bm25_candidates = [
        c.get("chunk_id") for c in candidates if c.get("bm25_score", 0) > 0
    ]
    vector_candidates = [
        c.get("chunk_id") for c in candidates if c.get("vector_score", 0) > 0
    ]

    # Step 3: Reranking.  A configured reranker failure is recorded as a
    # degraded result; it is never represented as a successful rerank.
    if use_rerank:
        try:
            candidates = rerank(query, candidates, top_k)
            reranker_status = "OK"
        except Exception as exc:
            reranker_status = "FAILED"
            _record_error("reranker", exc, code="RERANK_FAILED")
            candidates = candidates[:top_k]
    else:
        candidates = candidates[:top_k]

    # Step 4: Quality Gate.  Call the actual helper signature:
    # assess_quality(reranked_results, query_filters=None).  If the gate
    # cannot run, return ERROR metadata and do not invent a HyDE trigger.
    qg_mod = _get_quality_gate_module()
    if qg_mod and hasattr(qg_mod, "assess_quality"):
        try:
            quality_gate_result = qg_mod.assess_quality(
                candidates,
                query_filters=effective_filters,
            )
        except Exception as exc:
            quality_gate_result = {
                "status": "ERROR",
                "score": 0.0,
                "top1_score": 0.0,
                "avg_score": 0.0,
                "result_count": len(candidates),
                "doc_source_count": 0,
                "metadata_match_count": 0,
                "reasons": ["quality_gate_error"],
                "error": str(exc),
            }
            _record_error("quality_gate", exc, code="QUALITY_GATE_FAILED")
    else:
        quality_gate_result = {
            "status": "ERROR",
            "score": 0.0,
            "top1_score": 0.0,
            "avg_score": 0.0,
            "result_count": len(candidates),
            "doc_source_count": 0,
            "metadata_match_count": 0,
            "reasons": ["quality_gate_unavailable"],
            "error": "quality gate service is unavailable",
        }
        _record_error("quality_gate", quality_gate_result["error"], code="UNAVAILABLE")

    # Step 5: Adaptive HyDE.  HyDE receives context chunks, not a DB session
    # and project id.  Its generated text is used only as a retrieval query;
    # it is never returned as evidence or treated as a citation.
    if adaptive_requested and (hyde or deep):
        trigger_mod = qg_mod
        should_trigger = False
        if trigger_mod and hasattr(trigger_mod, "should_trigger_hyde"):
            try:
                should_trigger = bool(trigger_mod.should_trigger_hyde(quality_gate_result))
            except Exception as exc:
                _record_error("hyde_trigger", exc, code="TRIGGER_FAILED")
        else:
            _record_error("hyde_trigger", "quality gate trigger is unavailable", code="UNAVAILABLE")

        if should_trigger:
            if not ENABLE_HYDE:
                hyde_result = {
                    "hypothetical_text": query,
                    "helper_used": False,
                    "status": "DISABLED",
                    "error": "HyDE disabled by configuration",
                }
            else:
                hyde_mod = _get_query_rewrite_module()
                if not hyde_mod or not hasattr(hyde_mod, "hyde_generate"):
                    hyde_result = {
                        "hypothetical_text": query,
                        "helper_used": False,
                        "status": "UNAVAILABLE",
                        "error": "HyDE service is unavailable",
                    }
                    _record_error("hyde", hyde_result["error"], code="UNAVAILABLE")
                else:
                    try:
                        raw_hyde = hyde_mod.hyde_generate(
                            query,
                            context_chunks=candidates,
                            max_hints=3,
                        )
                        hyde_result = _as_dict(raw_hyde)
                        if not hyde_result:
                            raise TypeError("HyDE returned no structured result")
                        status = str(hyde_result.get("status") or "OK")
                        error = hyde_result.get("error")
                        hypothetical_text = hyde_result.get("hypothetical_text")
                        helper_used = bool(hyde_result.get("helper_used"))
                        if status != "OK" or not helper_used or not isinstance(hypothetical_text, str) or not hypothetical_text.strip():
                            _record_error("hyde", error or status, code=status or "FAILED")
                        else:
                            hyde_used = True
                            hyde_search_diagnostics: dict = {}
                            hyde_candidates = _hybrid_search(
                                db,
                                project_id,
                                hypothetical_text.strip(),
                                effective_filters,
                                top_k,
                                diagnostics=hyde_search_diagnostics,
                            )
                            if hyde_search_diagnostics.get("vector_status", "").startswith("FAILED"):
                                _record_error(
                                    "vector_search_hyde",
                                    hyde_search_diagnostics.get("vector_error", "vector search failed"),
                                    code=hyde_search_diagnostics["vector_status"],
                                )
                            search_diagnostics["hyde"] = hyde_search_diagnostics
                            candidates = _merge_with_rrf(candidates, hyde_candidates)
                            if use_rerank:
                                try:
                                    candidates = rerank(query, candidates, top_k)
                                    reranker_status = "OK"
                                except Exception as exc:
                                    reranker_status = "FAILED"
                                    _record_error("reranker_post_hyde", exc, code="RERANK_FAILED")
                                    candidates = candidates[:top_k]
                            else:
                                candidates = candidates[:top_k]

                            logger.info(
                                "HyDE triggered and applied, merged %d candidates",
                                len(hyde_candidates),
                            )
                            try:
                                quality_gate_result = qg_mod.assess_quality(
                                    candidates,
                                    query_filters=effective_filters,
                                )
                            except Exception as exc:
                                quality_gate_result = {
                                    "status": "ERROR",
                                    "score": 0.0,
                                    "top1_score": 0.0,
                                    "avg_score": 0.0,
                                    "result_count": len(candidates),
                                    "doc_source_count": 0,
                                    "metadata_match_count": 0,
                                    "reasons": ["post_hyde_quality_gate_error"],
                                    "error": str(exc),
                                }
                                _record_error("quality_gate_post_hyde", exc, code="QUALITY_GATE_FAILED")
                    except Exception as exc:
                        _record_error("hyde", exc, code="HYDE_FAILED")
                        hyde_result = {
                            "hypothetical_text": query,
                            "helper_used": False,
                            "status": "ERROR",
                            "error": str(exc),
                        }

    quality_gate_dict = _as_dict(quality_gate_result)
    reranked_ids = [c.get("chunk_id") for c in candidates]

    # Calculate latency
    elapsed_ms = int((time.time() - start_time) * 1000)
    retrieval_status = (quality_gate_dict or {}).get("status", "ERROR")
    if pipeline_errors:
        retrieval_status = "DEGRADED"

    # === Log QueryLog 2.0 ===
    try:
        from datetime import datetime, timezone
        log_entry = QueryLog(
            project_id=project_id,
            query=original_query,
            filters_json=json.dumps(effective_filters, ensure_ascii=False),
            top_k=top_k,
            result_chunk_ids_json=json.dumps([x.get("chunk_id") for x in candidates]),
            mode="retrieve",
            embedding_backend=EMBEDDING_BACKEND,
            reranker_backend=RERANKER_BACKEND if use_rerank else "off",
            response_time_ms=elapsed_ms,
            rewrite_result_json=json.dumps(_as_dict(rewrite_result), ensure_ascii=False) if rewrite_result else "",
            hyde_used=hyde_used,
            quality_gate_json=json.dumps(quality_gate_dict, ensure_ascii=False) if quality_gate_dict else "",
            bm25_candidates_json=json.dumps(bm25_candidates, ensure_ascii=False),
            vector_candidates_json=json.dumps(vector_candidates, ensure_ascii=False),
            reranked_json=json.dumps(reranked_ids, ensure_ascii=False),
            latency_ms=elapsed_ms,
            chunk_version=DEFAULT_CHUNK_VERSION,
            embedding_version=EMBEDDING_BACKEND,
            reranker_version=RERANKER_BACKEND if use_rerank else "off",
            retrieval_status=retrieval_status,
            deep_mode=deep,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        db.add(log_entry)
        db.commit()
    except Exception as exc:
        # Query logging is audit metadata and must not invalidate otherwise
        # valid evidence.  Roll back only the failed log transaction and keep
        # the failure visible in server logs.
        logger.warning("Failed to write QueryLog 2.0: %s", exc)
        db.rollback()

    logger.info(
        "Query '%s...' returned %d results in %dms (rewrite=%s, hyde=%s, deep=%s, status=%s)",
        original_query[:50],
        len(candidates),
        elapsed_ms,
        bool(rewrite_result and (_as_dict(rewrite_result) or {}).get("status") == "OK"),
        hyde_used,
        deep,
        retrieval_status,
    )

    # Keep the legacy list response for ordinary retrieval.  Any adaptive
    # request receives the complete pipeline status, including step failures;
    # no rewrite/quality/HyDE error is hidden behind a normal-looking list.
    if not adaptive_requested:
        return candidates

    rewrite_dict = _as_dict(rewrite_result)
    hyde_dict = _as_dict(hyde_result)
    return {
        "results": candidates,
        "rewrite_used": bool(rewrite_dict and rewrite_dict.get("status") == "OK"),
        "hyde_used": hyde_used,
        "deep_mode": deep,
        "rewrite_result": rewrite_dict,
        "hyde_result": hyde_dict,
        "quality_gate": quality_gate_dict,
        "pipeline_errors": pipeline_errors,
        "reranker_status": reranker_status,
        "retrieval_status": retrieval_status,
        "search_diagnostics": search_diagnostics,
        "effective_filters": effective_filters,
        "latency_ms": elapsed_ms,
        "bm25_candidates": bm25_candidates,
        "vector_candidates": vector_candidates,
    }


def retrieve_deep(
    db: Session,
    project_id: int,
    query: str,
    filters: dict,
    top_k: int = 10,
    history: list = None
) -> dict:
    """Deep retrieval with full evidence pack.

    Returns a complete EvidencePack with all pipeline metadata and up to 8 evidence chunks.

    Args:
        db: Database session
        project_id: Project to search
        query: Search query
        filters: Metadata filters
        top_k: Number of results to return (capped at 8 for evidence)
        history: Optional conversation history for context

    Returns:
        dict with EvidencePack structure:
        {
            "query": str,
            "project_id": int,
            "rewrite_used": bool,
            "hyde_used": bool,
            "rewrite_result": dict | None,
            "hyde_result": dict | None,
            "quality_gate": dict,
            "evidence": list[dict],  # Up to 8 chunks
            "latency_ms": int
        }
    """
    from ..config import EVIDENCE_PER_QUERY

    effective_top_k = min(top_k, EVIDENCE_PER_QUERY, 8)

    # Call retrieve with deep mode enabled
    result = retrieve(
        db=db,
        project_id=project_id,
        query=query,
        filters=filters,
        top_k=effective_top_k,
        use_rerank=True,
        deep=True
    )

    # If legacy mode returned list directly
    if isinstance(result, list):
        evidence = result[:effective_top_k]
        return {
            "query": query,
            "project_id": project_id,
            "rewrite_used": False,
            "hyde_used": False,
            "rewrite_result": None,
            "hyde_result": None,
            "quality_gate": None,
            "evidence": evidence,
            "latency_ms": 0
        }

    # Standardize to EvidencePack format
    return {
        "query": query,
        "project_id": project_id,
        "rewrite_used": result.get("rewrite_used", False),
        "hyde_used": result.get("hyde_used", False),
        "rewrite_result": result.get("rewrite_result"),
        "hyde_result": result.get("hyde_result"),
        "quality_gate": result.get("quality_gate"),
        "evidence": result.get("results", [])[:effective_top_k],
        "latency_ms": result.get("latency_ms", 0)
    }


def retrieve_explain(
    db: Session,
    project_id: int,
    query: str,
    filters: dict,
    top_k: int = 10
) -> dict:
    """Explainable retrieval with full scoring breakdown.

    Returns detailed scoring information for each result including
    vector score, BM25 score, and matched keywords.

    Args:
        db: Database session
        project_id: Project to search
        query: Search query
        filters: Metadata filters
        top_k: Number of results to return

    Returns:
        dict with explain structure:
        {
            "query": str,
            "project_id": int,
            "items": [
                {
                    "chunk_id": int,
                    "document_id": int,
                    "filename": str,
                    "heading_path": str,
                    "title_chain": str,
                    "rerank_score": float,
                    "vector_score": float,
                    "bm25_score": float,
                    "matched_keywords": list[str],
                    "metadata_matched": dict,
                },
                ...
            ],
            "quality_gate": dict
        }
    """
    # Get results (use deep mode to get full pipeline info)
    result = retrieve(
        db=db,
        project_id=project_id,
        query=query,
        filters=filters,
        top_k=top_k,
        use_rerank=True,
        deep=True
    )

    # Extract quality gate from result
    quality_gate = None
    if isinstance(result, dict):
        quality_gate = result.get("quality_gate")
        results = result.get("results", [])
    else:
        results = result

    # Attach explanation metadata
    query_tokens = tokens(query)
    results = _attach_explain_metadata(results, query_tokens)
    explained_items = []

    for item in results[:top_k]:
        explained_items.append({
            "chunk_id": item.get("chunk_id"),
            "document_id": item.get("document_id"),
            "filename": item.get("filename", ""),
            "heading_path": item.get("heading_path", ""),
            "title_chain": item.get("title_chain", ""),
            "rerank_score": item.get("rerank_score", item.get("score", 0.0)),
            "vector_score": item.get("vector_score", 0.0),
            "bm25_score": item.get("bm25_score", 0.0),
            "matched_keywords": item.get("matched_keywords", []),
            "metadata_matched": item.get("metadata_matched", {}),
        })

    return {
        "query": query,
        "project_id": project_id,
        "items": explained_items,
        "quality_gate": quality_gate
    }


def get_query_stats(db: Session, project_id: Optional[int] = None) -> dict:
    """Get query statistics.

    Args:
        db: Database session
        project_id: Optional project filter

    Returns:
        Dict with query statistics
    """
    from sqlalchemy import func

    stmt = select(
        func.count(QueryLog.id),
        func.avg(QueryLog.response_time_ms),
        func.max(QueryLog.response_time_ms)
    )

    if project_id:
        stmt = stmt.where(QueryLog.project_id == project_id)

    result = db.execute(stmt).first()

    return {
        "total_queries": result[0] or 0,
        "avg_response_ms": round(result[1] or 0, 2),
        "max_response_ms": result[2] or 0,
    }
