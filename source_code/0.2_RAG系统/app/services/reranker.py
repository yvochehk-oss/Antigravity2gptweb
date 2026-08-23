"""Reranker service with improved error handling and batching."""
from ..config import RERANKER_BACKEND, RERANKER_MODEL, RERANK_TOP_N
from ..logging_config import get_logger

logger = get_logger(__name__)

# Maximum input to reranker to prevent payload explosion
MAX_RERANK_INPUT = 100


class RerankerError(Exception):
    """Raised when reranking fails."""
    pass


def _get_model():
    """Load and cache reranker model.

    Returns:
        Loaded FlagReranker instance
    """
    if not hasattr(_get_model, "model"):
        try:
            from FlagEmbedding import FlagReranker
            _get_model.model = FlagReranker(RERANKER_MODEL, use_fp16=False)
            logger.info(f"Loaded reranker model: {RERANKER_MODEL}")
        except Exception as e:
            logger.error(f"Failed to load reranker: {e}")
            raise RerankerError(f"Model load failed: {e}")
    return _get_model.model


def rerank(
    query: str,
    rows: list[dict],
    top_k: int
) -> list[dict]:
    """Rerank search results using BGE reranker.

    Args:
        query: Original search query
        rows: List of candidate results
        top_k: Number of results to return

    Returns:
        Reordered list of results
    """
    if not rows:
        return []

    # Check if reranker is disabled
    if RERANKER_BACKEND in ("", "none", "off"):
        return rows[:top_k]

    # Limit input size
    rows_to_rerank = rows[:MAX_RERANK_INPUT]
    remaining = rows[MAX_RERANK_INPUT:]

    if RERANKER_BACKEND == "bge_v2_m3":
        try:
            model = _get_model()

            # Build query-document pairs
            pairs = []
            for x in rows_to_rerank:
                text = (x.get("heading_path", "") + "\n" + x.get("text", ""))[:10000]
                pairs.append([query, text])

            # Compute scores
            scores = model.compute_score(pairs, normalize=True)

            if isinstance(scores, (int, float)):
                scores = [float(scores)]

            # Attach scores and sort
            for x, s in zip(rows_to_rerank, scores):
                x["rerank_score"] = round(float(s), 6)

            rows_to_rerank.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)

            logger.debug(
                f"Reranked {len(rows_to_rerank)} results, "
                f"top score: {rows_to_rerank[0].get('rerank_score', 0) if rows_to_rerank else 0}"
            )

        except Exception as e:
            logger.warning(f"Reranking failed, returning BM25 order: {e}")
            return rows[:top_k]

    # Combine with remaining (sorted by original score)
    result = rows_to_rerank + remaining
    return result[:top_k]


def reranker_runtime() -> dict:
    """Get reranker runtime information.

    Returns:
        Dict with backend, model, and status information
    """
    backend = RERANKER_BACKEND
    model = RERANKER_MODEL if backend == "bge_v2_m3" else ""

    model_loaded = False
    if backend == "bge_v2_m3":
        model_loaded = hasattr(_get_model, "model")

    return {
        "backend": backend,
        "model": model,
        "model_loaded": model_loaded,
        "max_input": MAX_RERANK_INPUT,
    }


def clear_model_cache():
    """Clear cached reranker model."""
    if hasattr(_get_model, "model"):
        del _get_model.model
        logger.info("Cleared reranker model cache")
