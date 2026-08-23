"""User feedback tracking and analysis for RAG query quality.

Provides CRUD for QueryFeedback records and analytics on negative feedback
to drive systematic improvement of retrieval quality.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from ..logging_config import get_logger
from ..models import QueryFeedback, QueryLog

logger = get_logger(__name__)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _feedback_to_dict(fb: QueryFeedback) -> dict[str, Any]:
    return {
        "id": fb.id,
        "query_log_id": fb.query_log_id,
        "project_id": fb.project_id,
        "helpful": fb.helpful,
        "reason": fb.reason,
        "correction": fb.correction,
        "comment": fb.comment,
        "created_at": fb.created_at,
    }


# ----------------------------------------------------------------------
# Core CRUD
# ----------------------------------------------------------------------

def record_feedback(
    db,
    query_log_id: int,
    project_id: int,
    helpful: bool,
    reason: str = "",
    correction: str = "",
    comment: str = "",
) -> QueryFeedback:
    """Record user feedback for a query.

    Args:
        db:            SQLAlchemy session.
        query_log_id:  QueryLog.id this feedback refers to.
        project_id:    Project.id this query belongs to.
        helpful:       True = positive, False = negative.
        reason:        Short reason code (e.g. "irrelevant", "incomplete").
        correction:    User's suggested correction (free text).
        comment:       Optional general comment.

    Returns:
        The created QueryFeedback record.
    """
    fb = QueryFeedback(
        query_log_id=query_log_id,
        project_id=project_id,
        helpful=helpful,
        reason=reason,
        correction=correction,
        comment=comment,
        created_at=_now(),
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)

    logger.info(
        f"Feedback recorded: query_log={query_log_id} helpful={helpful} reason='{reason}'"
    )
    return fb


def get_feedback_for_query(
    db,
    query_log_id: int,
) -> list[dict[str, Any]]:
    """Return all feedback entries for a specific query log.

    Args:
        db:            SQLAlchemy session.
        query_log_id:  QueryLog.id to look up.

    Returns:
        List of feedback dicts, newest first.
    """
    rows = db.scalars(
        select(QueryFeedback)
        .where(QueryFeedback.query_log_id == query_log_id)
        .order_by(QueryFeedback.created_at.desc())
    ).all()

    return [_feedback_to_dict(r) for r in rows]


# ----------------------------------------------------------------------
# Analytics
# ----------------------------------------------------------------------

def get_negative_feedback_stats(
    db,
    days: int = 30,
) -> dict[str, Any]:
    """Summarise negative feedback over the last N days.

    Args:
        db:    SQLAlchemy session.
        days:  Look-back window in days.

    Returns:
        {
            "by_reason": {reason: count},
            "total_negative": N,
            "total_positive": M,
            "period_days": days,
        }
    """
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    cutoff_str = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(timespec="seconds")

    # Total counts
    total_neg = db.scalar(
        select(func.count(QueryFeedback.id))
        .where(QueryFeedback.helpful == False)  # noqa: E712
        .where(QueryFeedback.created_at >= cutoff_str)
    ) or 0

    total_pos = db.scalar(
        select(func.count(QueryFeedback.id))
        .where(QueryFeedback.helpful == True)  # noqa: E712
        .where(QueryFeedback.created_at >= cutoff_str)
    ) or 0

    # Breakdown by reason
    reason_counts = db.execute(
        select(
            QueryFeedback.reason,
            func.count(QueryFeedback.id).label("count"),
        )
        .where(QueryFeedback.helpful == False)  # noqa: E712
        .where(QueryFeedback.created_at >= cutoff_str)
        .group_by(QueryFeedback.reason)
        .order_by(func.count(QueryFeedback.id).desc())
    ).all()

    by_reason = {row.reason: row.count for row in reason_counts}

    logger.info(
        f"Feedback stats ({days}d): negative={total_neg} positive={total_pos}"
    )

    return {
        "by_reason": by_reason,
        "total_negative": total_neg,
        "total_positive": total_pos,
        "period_days": days,
    }


def apply_feedback_to_audit(
    db,
    project_id: int,
) -> dict[str, Any]:
    """Aggregate negative feedback for a project and produce actionable insights.

    This function helps understand *why* users are dissatisfied and suggests
    concrete steps to improve the RAG pipeline.

    Args:
        db:         SQLAlchemy session.
        project_id: Project.id to analyse.

    Returns:
        {
            "common_issues": [
                {"reason": "...", "count": N, "sample_queries": [...]}
            ],
            "recommendations": [
                "Consider adding more documents about ...",
                "Review chunk boundaries for ...",
            ],
            "total_negative": N,
            "analysed_at": "...",
        }
    """
    cutoff = datetime.now(timezone.utc).timestamp() - 90 * 86400
    cutoff_str = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(timespec="seconds")

    # Fetch all negative feedback for this project
    neg_rows = db.scalars(
        select(QueryFeedback)
        .where(QueryFeedback.project_id == project_id)
        .where(QueryFeedback.helpful == False)  # noqa: E712
        .where(QueryFeedback.created_at >= cutoff_str)
        .order_by(QueryFeedback.created_at.desc())
    ).all()

    total_negative = len(neg_rows)

    # Group by reason
    from collections import defaultdict
    by_reason: dict[str, list[QueryFeedback]] = defaultdict(list)
    for fb in neg_rows:
        by_reason[fb.reason].append(fb)

    # Build common_issues with sample queries
    common_issues = []
    for reason, feedbacks in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        # Fetch up to 3 representative query texts
        sample_queries = []
        for fb in feedbacks[:3]:
            qlog = db.get(QueryLog, fb.query_log_id)
            if qlog:
                sample_queries.append(qlog.query[:120])

        common_issues.append({
            "reason": reason or "(no reason given)",
            "count": len(feedbacks),
            "sample_queries": sample_queries,
        })

    # Build recommendations based on dominant reasons
    recommendations = []
    for issue in common_issues[:5]:
        reason = issue["reason"].lower()
        if "irrelevant" in reason or "wrong" in reason:
            recommendations.append(
                f"Topic '{issue['reason']}' appears {issue['count']} times: "
                "review retrieval keyword weights or add domain-specific synonyms."
            )
        elif "incomplete" in reason or "missing" in reason:
            recommendations.append(
                f"'{issue['reason']}' noted {issue['count']} times: "
                "consider increasing top_k or adding more source documents."
            )
        elif "outdated" in reason or "stale" in reason:
            recommendations.append(
                f"'{issue['reason']}' noted {issue['count']} times: "
                "re-index with latest document versions."
            )
        else:
            recommendations.append(
                f"Reason '{issue['reason']}' ({issue['count']} occurrences): "
                "investigate retrieval quality for this topic."
            )

    if not recommendations:
        recommendations.append("No dominant negative patterns detected – keep monitoring.")

    logger.info(
        f"apply_feedback_to_audit: project={project_id} "
        f"negative={total_negative} issues={len(common_issues)}"
    )

    return {
        "common_issues": common_issues,
        "recommendations": recommendations,
        "total_negative": total_negative,
        "analysed_at": _now(),
    }
