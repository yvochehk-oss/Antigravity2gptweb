"""Optimized retrieval service with BM25 and vector fusion.

Provides hybrid search combining lexical (BM25) and semantic (vector) retrieval.
Includes V0.3 Adaptive Retrieval Pipeline with Query Rewrite, Quality Gate, and HyDE.
"""
import json
import math
import re
import time
from collections import Counter
from typing import Optional, Any
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import Chunk, Document, QueryLog
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
    if not BM25_USE_POSTGRES_FTS:
        return []

    try:
        ts_query = " & ".join(tokens(query))
        stmt = base_stmt.where(
            text(f"to_tsvector('simple', search_text) @@ to_tsquery('simple', :tsq)")
        ).params(tsq=ts_query).order_by(
            text(f"ts_rank(to_tsvector('simple', search_text), to_tsquery('simple', :tsq)) DESC")
        ).limit(limit)

        return db.execute(stmt).all()
    except Exception as e:
        logger.warning(f"PostgreSQL FTS failed, falling back to Python BM25: {e}")
        return []


def _base_stmt(project_id: int, filters: dict):
    """Build base query statement with filters.

    Args:
        project_id: Project ID
        filters: Metadata filters

    Returns:
        SQLAlchemy select statement
    """
    stmt = select(Chunk, Document).join(
        Document, Chunk.document_id == Document.id
    ).where(
        Chunk.project_id == project_id,
        Document.version_status == "effective",
        Document.duplicate_of_id.is_(None)
    )

    field_map = {
        "document_type": Document.document_type,
        "entity_code": Document.entity_code,
        "counterparty_code": Document.counterparty_code,
        "business_category": Document.business_category,
        "tax_category": Document.tax_category,
        "period": Document.period,
        "confidentiality": Document.confidentiality
    }

    for key, col in field_map.items():
        val = filters.get(key)
        if val:
            vals = val if isinstance(val, list) else [val]
            stmt = stmt.where(col.in_([str(x) for x in vals]))

    return stmt


def _hybrid_search(
    db: Session,
    project_id: int,
    query: str,
    filters: dict,
    top_k: int,
    candidate_limit: int = None
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

    qvec = embed(query)
    qtokens = tokens(query)
    base = _base_stmt(project_id, filters)

    row_map = {}  # chunk_id -> (chunk, document)
    score_map = {}  # chunk_id -> fused score
    vector_scores = {}  # chunk_id -> raw vector score
    bm25_scores = {}  # chunk_id -> raw BM25 score

    # === Vector Search (PostgreSQL pgvector) ===
    if IS_POSTGRES and len(qvec) == EMBEDDING_DIM:
        try:
            vstmt = base.where(
                Chunk.embedding.is_not(None)
            ).order_by(
                Chunk.embedding.cosine_distance(qvec)
            ).limit(candidate_limit)

            vrows = db.execute(vstmt).all()

            for rank, (c, d) in enumerate(vrows, 1):
                row_map[c.id] = (c, d)
                rr_score = 1.0 / (RRF_K + rank)
                score_map[c.id] = rr_score
                vector_scores[c.id] = 1.0 / rank  # Raw vector rank score

            logger.debug(f"Vector search returned {len(vrows)} candidates")

        except Exception as e:
            logger.warning(f"Vector search failed: {e}")

    # === Lexical Search (BM25) ===
    use_python_bm25 = not IS_POSTGRES or not BM25_USE_POSTGRES_FTS
    lexical_rows = _pg_fts_search(db, base, query, candidate_limit)

    if not lexical_rows:
        lexical_rows = db.execute(base.limit(BM25_CANDIDATE_LIMIT)).all()
        use_python_bm25 = True

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

    # Track pipeline state
    rewrite_result = None
    hyde_result = None
    hyde_used = False
    quality_gate_result = None
    original_query = query
    bm25_candidates = []
    vector_candidates = []

    # Step 1: Query Rewrite
    if (rewrite or deep) and ENABLE_QUERY_REWRITE:
        qr_mod = _get_query_rewrite_module()
        if qr_mod and hasattr(qr_mod, 'rewrite_query'):
            try:
                rewrite_result = qr_mod.rewrite_query(query, db, project_id)
                if rewrite_result and rewrite_result.get("rewritten_query"):
                    query = rewrite_result["rewritten_query"]
                    logger.info(f"Query rewritten: '{original_query[:30]}...' -> '{query[:30]}...'")
            except Exception as e:
                logger.warning(f"Query rewrite failed, using original: {e}")
                query = original_query
        else:
            logger.debug("Query rewrite module not available, using original query")

    # Step 2: Hybrid Search
    candidates = _hybrid_search(db, project_id, query, filters, top_k)

    # Store raw candidates for logging
    bm25_candidates = [c.get("chunk_id") for c in candidates if c.get("bm25_score", 0) > 0]
    vector_candidates = [c.get("chunk_id") for c in candidates if c.get("vector_score", 0) > 0]

    # Step 3: Reranking
    if use_rerank:
        candidates = rerank(query, candidates, top_k)

    # Store reranked chunk IDs for logging
    reranked_ids = [c.get("chunk_id") for c in candidates]

    # Step 4: Quality Gate Assessment
    qg_mod = _get_quality_gate_module()
    if qg_mod and hasattr(qg_mod, 'assess_quality'):
        try:
            quality_gate_result = qg_mod.assess_quality(query, candidates, top_k)
        except Exception as e:
            logger.warning(f"Quality gate assessment failed: {e}")
            quality_gate_result = {"status": "unknown", "reason": str(e)}
    else:
        # Simple fallback quality assessment
        if candidates:
            top_score = candidates[0].get("score", 0) if candidates else 0
            quality_gate_result = {
                "status": "pass" if top_score > 0.3 else "low",
                "top1_score": top_score,
                "avg_score": sum(c.get("score", 0) for c in candidates[:3]) / min(3, len(candidates)) if candidates else 0,
                "candidate_count": len(candidates)
            }

    # Step 5: Adaptive HyDE
    if hyde or deep:
        trigger_mod = _get_quality_gate_module()
        should_trigger = False

        if trigger_mod and hasattr(trigger_mod, 'should_trigger_hyde'):
            try:
                should_trigger = trigger_mod.should_trigger_hyde(quality_gate_result)
            except Exception as e:
                logger.warning(f"HyDE trigger check failed: {e}")
        else:
            # Fallback: trigger if quality is low
            should_trigger = quality_gate_result.get("status") == "low"

        if should_trigger and ENABLE_HYDE:
            hyde_mod = _get_query_rewrite_module()
            if hyde_mod and hasattr(hyde_mod, 'hyde_generate'):
                try:
                    hyde_result = hyde_mod.hyde_generate(query, db, project_id)
                    if hyde_result and hyde_result.get("hypothetical_text"):
                        hyde_used = True
                        hypothetical_text = hyde_result["hypothetical_text"]

                        # Do second hybrid search with hypothetical text
                        hyde_candidates = _hybrid_search(
                            db, project_id, hypothetical_text, filters, top_k
                        )

                        # Merge with RRF
                        candidates = _merge_with_rrf(candidates, hyde_candidates)

                        # Re-rerank after merge
                        if use_rerank:
                            candidates = rerank(query, candidates, top_k)

                        logger.info(f"HyDE triggered and applied, merged {len(hyde_candidates)} candidates")

                        # Re-assess quality after HyDE
                        if qg_mod and hasattr(qg_mod, 'assess_quality'):
                            try:
                                quality_gate_result = qg_mod.assess_quality(query, candidates, top_k)
                            except Exception as e:
                                logger.warning(f"Post-HyDE quality gate failed: {e}")

                except Exception as e:
                    logger.warning(f"HyDE generation failed: {e}")

    # Calculate latency
    elapsed_ms = int((time.time() - start_time) * 1000)

    # === Log QueryLog 2.0 ===
    try:
        qr_mod_for_log = _get_query_rewrite_module()
        gate_mod_for_log = _get_quality_gate_module()

        log_entry = QueryLog(
            project_id=project_id,
            query=original_query,
            filters_json=json.dumps(filters, ensure_ascii=False),
            top_k=top_k,
            result_chunk_ids_json=json.dumps([x.get("chunk_id") for x in candidates]),
            mode="retrieve",
            embedding_backend=EMBEDDING_BACKEND,
            reranker_backend=RERANKER_BACKEND if use_rerank else "off",
            response_time_ms=elapsed_ms,
            # V0.3 fields
            rewrite_result_json=json.dumps(rewrite_result, ensure_ascii=False) if rewrite_result else "",
            hyde_used=hyde_used,
            quality_gate_json=json.dumps(quality_gate_result, ensure_ascii=False) if quality_gate_result else "",
            bm25_candidates_json=json.dumps(bm25_candidates, ensure_ascii=False),
            vector_candidates_json=json.dumps(vector_candidates, ensure_ascii=False),
            reranked_json=json.dumps(reranked_ids, ensure_ascii=False),
            latency_ms=elapsed_ms,
            chunk_version=DEFAULT_CHUNK_VERSION,
            embedding_version=EMBEDDING_BACKEND,
            reranker_version=RERANKER_BACKEND if use_rerank else "off",
            retrieval_status=quality_gate_result.get("status", "unknown") if quality_gate_result else "unknown",
            deep_mode=deep,
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to write QueryLog 2.0: {e}")
        db.rollback()

    logger.info(
        f"Query '{original_query[:50]}...' returned {len(candidates)} results in {elapsed_ms}ms"
        f" (rewrite={bool(rewrite_result)}, hyde={hyde_used}, deep={deep})"
    )

    # === Return based on mode ===
    # If no adaptive features requested, return legacy list format (backward compatible)
    if not rewrite_result and not hyde_used and not deep and not rewrite and not hyde:
        return candidates

    # Otherwise return full dict with pipeline metadata
    def _gate_to_dict(gate):
        if gate is None:
            return None
        if isinstance(gate, dict):
            return gate
        if hasattr(gate, '__dict__'):
            return {k: v for k, v in gate.__dict__.items() if not k.startswith('_')}
        return str(gate)

    def _result_to_dict(result):
        if result is None:
            return None
        if isinstance(result, dict):
            return result
        if hasattr(result, '__dict__'):
            return {k: v for k, v in result.__dict__.items() if not k.startswith('_')}
        return str(result)

    return {
        "results": candidates,
        "rewrite_used": bool(rewrite_result),
        "hyde_used": hyde_used,
        "deep_mode": deep,
        "rewrite_result": _result_to_dict(rewrite_result),
        "hyde_result": _result_to_dict(hyde_result),
        "quality_gate": _gate_to_dict(quality_gate_result),
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
    explained_items = []

    for item in results[:top_k]:
        explained_items.append({
            "chunk_id": item.get("chunk_id"),
            "document_id": item.get("document_id"),
            "filename": item.get("filename", ""),
            "heading_path": item.get("heading_path", ""),
            "title_chain": item.get("title_chain", ""),
            "rerank_score": item.get("score", 0.0),  # Reranker may override score field
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
