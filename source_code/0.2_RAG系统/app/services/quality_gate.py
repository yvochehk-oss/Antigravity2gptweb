"""Quality Gate service for retrieval result scoring.

Evaluates the reliability of reranked retrieval results and decides whether
Adaptive HyDE should be triggered when confidence is low.
"""
from dataclasses import dataclass, asdict
from ..config import (
    QUALITY_GATE_TOP1_MIN,
    QUALITY_GATE_AVG_MIN,
    QUALITY_GATE_MIN_EVIDENCE,
)
from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class QualityGate:
    """Quality assessment for reranked retrieval results.

    Attributes:
        status: One of GOOD | PARTIAL | LOW_CONFIDENCE | INSUFFICIENT_EVIDENCE
        score: Composite quality score in [0, 1]
        top1_score: Score of the highest-ranked result
        avg_score: Mean score across all results
        result_count: Number of results considered
        doc_source_count: Number of unique source documents represented
        metadata_match_count: Number of results matching at least one query filter
        reasons: Human-readable list of contributing signals
    """
    status: str
    score: float
    top1_score: float
    avg_score: float
    result_count: int
    doc_source_count: int
    metadata_match_count: int
    reasons: list[str]


def _result_score(result: dict) -> float:
    """Extract a numeric score from a reranked result.

    Prefers ``rerank_score`` when available, falls back to ``score``.
    Missing or non-numeric values are treated as 0.0.

    Args:
        result: A single reranked result dict.

    Returns:
        Float score in [0, 1] (clamped).
    """
    raw = result.get("rerank_score")
    if raw is None:
        raw = result.get("score", 0.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _metadata_match(result: dict, filters: dict) -> bool:
    """Check whether a result matches at least one filter field.

    The result is treated as a metadata dict; if a key in ``filters`` also
    exists on the result and the values are equal (string compare), the
    filter is considered satisfied.

    Args:
        result: A single reranked result dict.
        filters: Optional filter mapping such as ``{"entity_code": "E001"}``.

    Returns:
        True if at least one filter key matches the result.
    """
    if not filters:
        return False
    for key, want in filters.items():
        if want is None or want == "":
            continue
        got = result.get(key)
        if got is None:
            continue
        if isinstance(want, list):
            if str(got) in {str(x) for x in want}:
                return True
        else:
            if str(got) == str(want):
                return True
    return False


def assess_quality(
    reranked_results: list[dict],
    query_filters: dict | None = None,
) -> QualityGate:
    """Assess retrieval quality and produce a QualityGate verdict.

    The composite score combines the top-1 score (50%), average score (30%)
    and source-document diversity (20%) so that a single strong hit can still
    pass even when the rest of the list is noisy.

    Args:
        reranked_results: List of reranked results from the retriever.
        query_filters: Optional filters extracted from the rewritten query.

    Returns:
        QualityGate describing the verdict and contributing signals.
    """
    filters = query_filters or {}
    reasons: list[str] = []

    if not reranked_results:
        logger.debug("QualityGate: no reranked results")
        return QualityGate(
            status="INSUFFICIENT_EVIDENCE",
            score=0.0,
            top1_score=0.0,
            avg_score=0.0,
            result_count=0,
            doc_source_count=0,
            metadata_match_count=0,
            reasons=["no_results"],
        )

    scores = [_result_score(r) for r in reranked_results]
    top1_score = scores[0]
    avg_score = sum(scores) / len(scores)

    doc_source_count = len({
        r.get("document_id") for r in reranked_results if r.get("document_id") is not None
    })
    if doc_source_count == 0:
        # Fall back to using filename as identity when document_id is absent
        doc_source_count = len({
            r.get("document_code") or r.get("filename") or str(id(r))
            for r in reranked_results
        })

    metadata_match_count = sum(
        1 for r in reranked_results if _metadata_match(r, filters)
    )

    diversity = min(doc_source_count / 3.0, 1.0)
    score = 0.5 * top1_score + 0.3 * avg_score + 0.2 * diversity
    if score > 1.0:
        score = 1.0
    if score < 0.0:
        score = 0.0

    result_count = len(reranked_results)

    # Status ladder: GOOD > PARTIAL > LOW_CONFIDENCE > INSUFFICIENT_EVIDENCE
    if (
        top1_score >= QUALITY_GATE_TOP1_MIN
        and avg_score >= QUALITY_GATE_AVG_MIN
        and result_count >= QUALITY_GATE_MIN_EVIDENCE
    ):
        status = "GOOD"
    elif top1_score >= 0.4 and result_count >= 1:
        status = "PARTIAL"
    elif top1_score >= 0.2:
        status = "LOW_CONFIDENCE"
    else:
        status = "INSUFFICIENT_EVIDENCE"

    reasons.append(f"top1_score={round(top1_score, 4)}")
    reasons.append(f"avg_score={round(avg_score, 4)}")
    reasons.append(f"doc_diversity={doc_source_count}")
    if metadata_match_count:
        reasons.append(f"metadata_matches={metadata_match_count}")
    reasons.append(f"result_count={result_count}")
    reasons.append(f"status={status}")

    logger.debug(
        "QualityGate verdict: status=%s score=%.4f top1=%.4f avg=%.4f docs=%d results=%d",
        status, score, top1_score, avg_score, doc_source_count, result_count,
    )

    return QualityGate(
        status=status,
        score=round(score, 4),
        top1_score=round(top1_score, 4),
        avg_score=round(avg_score, 4),
        result_count=result_count,
        doc_source_count=doc_source_count,
        metadata_match_count=metadata_match_count,
        reasons=reasons,
    )


def should_trigger_hyde(gate: QualityGate) -> bool:
    """Decide whether Adaptive HyDE should be triggered for this gate.

    HyDE is enabled whenever the quality verdict is too low to trust the
    current results; that includes both ``LOW_CONFIDENCE`` and
    ``INSUFFICIENT_EVIDENCE``.

    Args:
        gate: QualityGate verdict produced by :func:`assess_quality`.

    Returns:
        True when Adaptive HyDE should run.
    """
    return gate.status in {"LOW_CONFIDENCE", "INSUFFICIENT_EVIDENCE"}


def gate_to_dict(gate: QualityGate) -> dict:
    """Convert a QualityGate dataclass into a Pydantic-friendly dict.

    Args:
        gate: QualityGate instance to serialize.

    Returns:
        Plain dict suitable for JSON serialization.
    """
    return asdict(gate)
