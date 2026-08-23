"""Reranker service with improved error handling and batching."""
from ..config import RERANKER_BACKEND, RERANKER_MODEL, RERANK_TOP_N
from ..logging_config import get_logger

logger = get_logger(__name__)

# Maximum input to reranker to prevent payload explosion
MAX_RERANK_INPUT = 100


class RerankerError(Exception):
    """Raised when reranking fails."""
    pass


class DirectTransformersReranker:
    """Robust local cross-encoder reranker based directly on transformers with GPU acceleration."""
    def __init__(self, model_path: str):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True).to(self.device)
        self.model.eval()
        self.torch = torch

    def compute_score(self, pairs: list[list[str]], normalize: bool = True) -> list[float]:
        if not pairs:
            return []
        with self.torch.no_grad():
            inputs = self.tokenizer(pairs, padding=True, truncation=True, return_tensors="pt", max_length=256).to(self.device)
            logits = self.model(**inputs, return_dict=True).logits.view(-1,).float()
            if normalize:
                scores = logits.sigmoid().tolist()
            else:
                scores = logits.tolist()
            if isinstance(scores, float):
                return [scores]
            return scores


def _get_model():
    """Load and cache reranker model.

    Returns:
        Loaded DirectTransformersReranker or FlagReranker instance
    """
    if not hasattr(_get_model, "model"):
        try:
            _get_model.model = DirectTransformersReranker(RERANKER_MODEL)
            logger.info(f"Loaded reranker via DirectTransformersReranker: {RERANKER_MODEL}")
        except Exception as e:
            logger.info(f"DirectTransformersReranker load failed ({e}), falling back to FlagReranker...")
            try:
                from FlagEmbedding import FlagReranker
                _get_model.model = FlagReranker(RERANKER_MODEL, use_fp16=False)
                logger.info(f"Loaded reranker via FlagReranker: {RERANKER_MODEL}")
            except Exception as e2:
                logger.error(f"Failed to load reranker: {e2}")
                raise RerankerError(f"Model load failed: {e2}")
    return _get_model.model


def rerank(
    query: str,
    rows: list[dict],
    top_k: int,
    *,
    raise_on_error: bool = True,
) -> list[dict]:
    """Rerank search results using BGE reranker.

    Args:
        query: Original search query
        rows: List of candidate results
        top_k: Number of results to return
        raise_on_error: Raise :class:`RerankerError` when the configured
            reranker cannot run.  This defaults to ``True`` so callers do not
            mistake lexical order for a successfully reranked result.  A
            compatibility caller may opt into the old degraded behaviour and
            must then surface that choice in its own response metadata.

    Returns:
        Reordered list of results
    """
    if not rows:
        return []

    # Check if reranker is disabled
    if RERANKER_BACKEND in ("", "none", "off"):
        return rows[:top_k]

    if RERANKER_BACKEND != "bge_v2_m3":
        error = f"unsupported reranker backend: {RERANKER_BACKEND}"
        logger.error(error)
        if raise_on_error:
            raise RerankerError(error)
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
            logger.error(f"Reranking failed: {e}")
            if raise_on_error:
                if isinstance(e, RerankerError):
                    raise
                raise RerankerError(f"reranker execution failed: {e}") from e
            logger.warning("Returning pre-rerank order because caller explicitly allowed degradation")
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
