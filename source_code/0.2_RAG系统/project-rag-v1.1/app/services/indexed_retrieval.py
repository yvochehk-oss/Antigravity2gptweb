"""Indexed PostgreSQL lexical retrieval installer.

Production PostgreSQL retrieval must never fall back to loading the filtered
corpus into Python merely because FTS returned no rows.  Chinese/English
lexical candidates are selected in PostgreSQL through the pg_trgm GIN index;
when that path is unavailable the hybrid search degrades to vector-only.
Portable/non-PostgreSQL mode keeps the legacy bounded local implementation.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, or_

from . import retrieval as legacy

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ENGLISH = re.compile(r"[A-Za-z0-9_]{3,}")


def lexical_terms(query: str, limit: int = 8) -> list[str]:
    """Return a small deterministic set of index-friendly terms.

    Chinese runs are converted to 3-character shingles so PostgreSQL's trigram
    index can serve ``ILIKE '%term%'`` predicates efficiently. Short Chinese
    queries are retained as-is rather than silently discarded.
    """
    terms: list[str] = []
    for run in _CJK_RUN.findall(query or ""):
        if len(run) >= 3:
            terms.extend(run[i : i + 3] for i in range(len(run) - 2))
        elif run:
            terms.append(run)
    terms.extend(x.lower() for x in _ENGLISH.findall(query or ""))

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            deduped.append(term)
        if len(deduped) >= limit:
            break
    return deduped


def _indexed_lexical_rows(db, base_stmt, query: str, limit: int):
    terms = lexical_terms(query)
    if not terms:
        return []
    predicates = [legacy.Chunk.search_text.ilike(f"%{term}%") for term in terms]
    # The WHERE predicates are served by gin_trgm_ops; similarity is only an
    # ordering signal over the already bounded/index-filtered candidate set.
    stmt = (
        base_stmt
        .where(or_(*predicates))
        .order_by(func.similarity(legacy.Chunk.search_text, query).desc())
        .limit(limit)
    )
    try:
        # Isolate an unavailable extension/index/function from the outer API
        # transaction. A lexical failure must not poison vector retrieval.
        with db.begin_nested():
            return db.execute(stmt).all()
    except Exception as exc:
        legacy.logger.warning("Indexed lexical search unavailable; vector-only: %s", exc)
        return []


def _hybrid_search_indexed(
    db,
    project_id: int,
    query: str,
    filters: dict,
    top_k: int,
    candidate_limit: int | None = None,
    diagnostics: dict | None = None,
) -> list[dict[str, Any]]:
    """PostgreSQL hybrid search without a Python corpus-scan fallback."""
    if not legacy.IS_POSTGRES:
        return _ORIGINAL_HYBRID(
            db,
            project_id,
            query,
            filters,
            top_k,
            candidate_limit=candidate_limit,
            diagnostics=diagnostics,
        )

    if candidate_limit is None:
        candidate_limit = min(
            legacy.BM25_CANDIDATE_LIMIT,
            max(legacy.RERANK_TOP_N, top_k * 3),
        )
    diagnostics = diagnostics if diagnostics is not None else {}
    qvec = legacy.embed(query)
    base = legacy._base_stmt(project_id, filters)

    row_map: dict[int, tuple[Any, Any]] = {}
    score_map: dict[int, float] = {}
    vector_scores: dict[int, float] = {}
    lexical_scores: dict[int, float] = {}

    if len(qvec) != legacy.EMBEDDING_DIM:
        diagnostics["vector_status"] = "FAILED_DIMENSION"
        diagnostics["vector_error"] = (
            f"query embedding dimension {len(qvec)} != configured {legacy.EMBEDDING_DIM}"
        )
    else:
        try:
            vstmt = (
                base.where(legacy.Chunk.embedding.is_not(None))
                .order_by(legacy.Chunk.embedding.op("<=>")(qvec))
                .limit(candidate_limit)
            )
            with db.begin_nested():
                vrows = db.execute(vstmt).all()
            for rank, (chunk, document) in enumerate(vrows, 1):
                row_map[chunk.id] = (chunk, document)
                score_map[chunk.id] = 1.0 / (legacy.RRF_K + rank)
                vector_scores[chunk.id] = 1.0 / rank
            diagnostics["vector_status"] = "OK"
        except Exception as exc:
            diagnostics["vector_status"] = "FAILED"
            diagnostics["vector_error"] = str(exc)
            legacy.logger.warning("Vector search failed: %s", exc)

    lexical_rows = _indexed_lexical_rows(db, base, query, candidate_limit)
    diagnostics["lexical_backend"] = "postgres_trigram" if lexical_rows else "vector_only"
    for rank, (chunk, document) in enumerate(lexical_rows, 1):
        row_map[chunk.id] = (chunk, document)
        score_map[chunk.id] = score_map.get(chunk.id, 0.0) + 1.0 / (legacy.RRF_K + rank)
        lexical_scores[chunk.id] = 1.0 / rank

    candidates: list[dict[str, Any]] = []
    for cid, score in sorted(score_map.items(), key=lambda kv: kv[1], reverse=True)[:candidate_limit]:
        chunk, document = row_map[cid]
        candidates.append({
            "score": round(float(score), 6),
            "vector_score": round(float(vector_scores.get(cid, 0.0)), 6),
            "bm25_score": round(float(lexical_scores.get(cid, 0.0)), 6),
            "chunk_id": chunk.id,
            "document_id": document.id,
            "document_code": document.document_code,
            "filename": document.filename,
            "document_type": document.document_type,
            "entity_code": document.entity_code,
            "business_role": legacy._business_role_for_entity(document.entity_code),
            "counterparty_code": document.counterparty_code,
            "business_category": document.business_category,
            "period": document.period,
            "heading_path": chunk.heading_path,
            "title_chain": getattr(chunk, "title_chain", ""),
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "content_type": chunk.content_type,
            "text": chunk.content,
        })
    return candidates


_ORIGINAL_HYBRID = legacy._hybrid_search
legacy._hybrid_search = _hybrid_search_indexed


def __getattr__(name: str):
    """Expose the legacy public retrieval surface after installing the patch."""
    return getattr(legacy, name)
