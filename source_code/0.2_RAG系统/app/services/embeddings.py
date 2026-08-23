"""Embedding service with improved error handling and fallback.

Provides BGE-M3 embeddings with graceful degradation to hash fallback.
"""
import math
import hashlib
import re
from ..config import EMBEDDING_BACKEND, EMBEDDING_MODEL, EMBEDDING_MAX_LENGTH, EMBEDDING_DIM
from ..logging_config import get_logger

logger = get_logger(__name__)

HASH_DIM = 256


class EmbeddingError(Exception):
    """Raised when embedding operation fails."""
    pass


class ModelLoadError(EmbeddingError):
    """Raised when embedding model fails to load."""
    pass


def _hash_embedding(text: str) -> list[float]:
    """Generate hash-based embedding for development/fallback.

    Uses character n-grams with consistent hashing for reproducibility.

    Args:
        text: Input text

    Returns:
        Normalized embedding vector of HASH_DIM dimensions
    """
    vec = [0.0] * HASH_DIM
    normalized = re.sub(r"\s+", " ", text.lower())
    grams = [normalized[i:i+3] for i in range(max(1, len(normalized)-2))]

    for g in grams:
        h = int(hashlib.blake2b(g.encode("utf-8"), digest_size=8).hexdigest(), 16)
        idx = h % HASH_DIM
        vec[idx] += 1.0 if (h >> 8) & 1 else -1.0

    norm = math.sqrt(sum(v*v for v in vec)) or 1.0
    return [v/norm for v in vec]


def _get_bge_model():
    """Load and cache BGE-M3 model.

    Returns:
        Loaded BGEM3FlagModel instance

    Raises:
        ModelLoadError: If model cannot be loaded
    """
    if not hasattr(_get_bge_model, "model"):
        try:
            from FlagEmbedding import BGEM3FlagModel
            # Use fp16=False for CPU/Apple Silicon stability
            _get_bge_model.model = BGEM3FlagModel(EMBEDDING_MODEL, use_fp16=False)
            logger.info(f"Loaded BGE-M3 model: {EMBEDDING_MODEL}")
        except ImportError as e:
            raise ModelLoadError(f"FlagEmbedding not installed: {e}")
        except Exception as e:
            raise ModelLoadError(f"Failed to load BGE model: {e}")

    return _get_bge_model.model


def embed_many(texts: list[str], raise_on_error: bool = False) -> list[list[float]]:
    """Generate embeddings for multiple texts.

    Args:
        texts: List of text strings to embed
        raise_on_error: If True, raise on model load failure; if False, use fallback

    Returns:
        List of embedding vectors

    Raises:
        ModelLoadError: If raise_on_error=True and model fails to load
    """
    if not texts:
        return []

    # Fallback: use hash embedding if backend is disabled
    if EMBEDDING_BACKEND == "hash_v1":
        logger.debug("Using hash embedding fallback")
        return [_hash_embedding(x) for x in texts]

    # BGE-M3 embedding
    if EMBEDDING_BACKEND == "bge_m3":
        try:
            model = _get_bge_model()
            batch_size = min(8, max(1, len(texts)))
            result = model.encode(
                texts,
                batch_size=batch_size,
                max_length=EMBEDDING_MAX_LENGTH
            )
            vectors = result["dense_vecs"]

            # Validate dimensions
            valid_vectors = []
            for v in vectors:
                vec_list = [float(x) for x in v]
                if len(vec_list) == EMBEDDING_DIM:
                    valid_vectors.append(vec_list)
                else:
                    logger.warning(
                        f"Invalid vector dimension {len(vec_list)}, expected {EMBEDDING_DIM}"
                    )
                    valid_vectors.append(_hash_embedding(texts[len(valid_vectors)]))

            logger.debug(f"Generated {len(valid_vectors)} embeddings with BGE-M3")
            return valid_vectors

        except ModelLoadError:
            if raise_on_error:
                raise
            logger.warning("BGE model unavailable, using hash fallback")
            return [_hash_embedding(x) for x in texts]
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            if raise_on_error:
                raise EmbeddingError(f"Embedding failed: {e}")
            logger.warning("Using hash fallback due to error")
            return [_hash_embedding(x) for x in texts]

    # Unknown backend, use fallback
    logger.warning(f"Unknown embedding backend '{EMBEDDING_BACKEND}', using hash fallback")
    return [_hash_embedding(x) for x in texts]


def embed(text: str, raise_on_error: bool = False) -> list[float]:
    """Generate embedding for a single text.

    Args:
        text: Text to embed
        raise_on_error: If True, raise on model load failure

    Returns:
        Embedding vector

    Raises:
        ModelLoadError: If raise_on_error=True and model fails to load
    """
    return embed_many([text], raise_on_error=raise_on_error)[0]


def cosine(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two vectors.

    Args:
        a: First vector
        b: Second vector

    Returns:
        Cosine similarity score (-1 to 1)
    """
    n = min(len(a), len(b))
    if not n:
        return 0.0

    dot_product = sum(a[i] * b[i] for i in range(n))
    norm_a = math.sqrt(sum(a[i] * a[i] for i in range(n))) or 1.0
    norm_b = math.sqrt(sum(b[i] * b[i] for i in range(n))) or 1.0

    return dot_product / (norm_a * norm_b)


def embedding_runtime() -> dict:
    """Get embedding runtime information for health checks.

    Returns:
        Dict with backend, model, and status information
    """
    backend = EMBEDDING_BACKEND
    model = EMBEDDING_MODEL if backend == "bge_m3" else "hash_v1"

    # Check if model is loaded
    model_loaded = False
    if backend == "bge_m3":
        model_loaded = hasattr(_get_bge_model, "model")

    return {
        "backend": backend,
        "model": model,
        "dimension": EMBEDDING_DIM,
        "max_length": EMBEDDING_MAX_LENGTH,
        "model_loaded": model_loaded,
    }


def clear_model_cache():
    """Clear the cached embedding model.

    Useful for forcing model reload after configuration changes.
    """
    if hasattr(_get_bge_model, "model"):
        del _get_bge_model.model
        logger.info("Cleared embedding model cache")
