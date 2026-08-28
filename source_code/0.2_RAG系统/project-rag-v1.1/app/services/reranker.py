"""Optional reranker service.

BGE-M3 remains the semantic embedding model.  The cross-encoder reranker is an
optional accuracy boost and is disabled by default so low-resource Windows
hosts do not load a second transformer model unless explicitly requested.
"""
import os

from ..config import RERANKER_BACKEND, RERANKER_MODEL, RERANK_TOP_N
from ..logging_config import get_logger

logger = get_logger(__name__)

MAX_RERANK_INPUT = 100
RERANKER_ENABLED = os.getenv("PROJECT_RAG_RERANKER_ENABLED", "0").strip().lower() in {
    "1", "true", "yes", "on",
}


class RerankerError(Exception):
    """Raised when an explicitly enabled reranker fails."""
    pass


class DirectTransformersReranker:
    """Local cross-encoder reranker loaded only when reranking is enabled."""

    def __init__(self, model_path: str):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.device = "mps" if torch.backends.mps.is_available() else (
            "cuda" if torch.cuda.is_available() else "cpu"
        )
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_path, local_files_only=True
        ).to(self.device)
        self.model.eval()
        self.torch = torch

    def compute_score(self, pairs: list[list[str]], normalize: bool = True) -> list[float]:
        if not pairs:
            return []
        with self.torch.no_grad():
            inputs = self.tokenizer(
                pairs,
                padding=True,
                truncation=True,
                return_tensors="pt",
                max_length=256,
            ).to(self.device)
            logits = self.model(**inputs, return_dict=True).logits.view(-1).float()
            scores = logits.sigmoid().tolist() if normalize else logits.tolist()
            if isinstance(scores, float):
                return [scores]
            return scores


def _get_model():
    """Load and cache the reranker model after the explicit enable gate."""
    if not RERANKER_ENABLED:
        raise RerankerError("reranker is disabled")
    if not hasattr(_get_model, "model"):
        try:
            _get_model.model = DirectTransformersReranker(RERANKER_MODEL)
            logger.info("Loaded reranker via DirectTransformersReranker: %s", RERANKER_MODEL)
        except Exception as exc:
            logger.info(
                "DirectTransformersReranker load failed (%s), falling back to FlagReranker...",
                exc,
            )
            try:
                from FlagEmbedding import FlagReranker

                _get_model.model = FlagReranker(RERANKER_MODEL, use_fp16=False)
                logger.info("Loaded reranker via FlagReranker: %s", RERANKER_MODEL)
            except Exception as fallback_exc:
                logger.error("Failed to load reranker: %s", fallback_exc)
                raise RerankerError(f"Model load failed: {fallback_exc}") from fallback_exc
    return _get_model.model


def rerank(
    query: str,
    rows: list[dict],
    top_k: int,
    *,
    raise_on_error: bool = True,
) -> list[dict]:
    """Optionally rerank search results using BGE reranker.

    When PROJECT_RAG_RERANKER_ENABLED=0 (the default), this function is a cheap
    pass-through and never imports torch/transformers or loads a reranker model.
    """
    if not rows:
        return []

    if not RERANKER_ENABLED or RERANKER_BACKEND in ("", "none", "off"):
        return rows[:top_k]

    if RERANKER_BACKEND != "bge_v2_m3":
        error = f"unsupported reranker backend: {RERANKER_BACKEND}"
        logger.error(error)
        if raise_on_error:
            raise RerankerError(error)
        return rows[:top_k]

    rows_to_rerank = rows[:MAX_RERANK_INPUT]
    remaining = rows[MAX_RERANK_INPUT:]

    try:
        model = _get_model()
        pairs = []
        for item in rows_to_rerank:
            text = (item.get("heading_path", "") + "\n" + item.get("text", ""))[:10000]
            pairs.append([query, text])

        scores = model.compute_score(pairs, normalize=True)
        if isinstance(scores, (int, float)):
            scores = [float(scores)]

        for item, score in zip(rows_to_rerank, scores):
            item["rerank_score"] = round(float(score), 6)
        rows_to_rerank.sort(key=lambda item: item.get("rerank_score", 0), reverse=True)

        logger.debug(
            "Reranked %s results, top score: %s",
            len(rows_to_rerank),
            rows_to_rerank[0].get("rerank_score", 0) if rows_to_rerank else 0,
        )
    except Exception as exc:
        logger.error("Reranking failed: %s", exc)
        if raise_on_error:
            if isinstance(exc, RerankerError):
                raise
            raise RerankerError(f"reranker execution failed: {exc}") from exc
        logger.warning("Returning pre-rerank order because caller explicitly allowed degradation")
        return rows[:top_k]

    return (rows_to_rerank + remaining)[:top_k]


def reranker_runtime() -> dict:
    """Return explicit enablement and model-loading state for health endpoints."""
    backend = RERANKER_BACKEND
    model = RERANKER_MODEL if RERANKER_ENABLED and backend == "bge_v2_m3" else ""
    model_loaded = (
        RERANKER_ENABLED
        and backend == "bge_v2_m3"
        and hasattr(_get_model, "model")
    )
    return {
        "enabled": RERANKER_ENABLED,
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
