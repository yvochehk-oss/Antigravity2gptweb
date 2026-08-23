"""QueryLog retention helpers.

The ``query_logs`` table is one of the largest in the database because
it stores the full retrieval trace (``bm25_candidates_json``,
``reranked_json``, embedding_json payloads, etc.).  This module provides
a deterministic purge routine so production deployments can keep the
table under a fixed retention window.

The default retention is 30 days, matching the upstream review report.
The CLI in :func:`prune_query_logs_cli` accepts an explicit ``--days``
flag and a ``--dry-run`` mode for safe inspection.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from ..config import QUERY_LOG_RETENTION_DAYS
from ..db import SessionLocal
from ..logging_config import get_logger
from ..models import QueryLog

logger = get_logger(__name__)

DEFAULT_RETENTION_DAYS = QUERY_LOG_RETENTION_DAYS


def _cutoff(retention_days: int, now: datetime | None = None) -> str:
    """Return the ISO timestamp below which query_logs must be pruned.

    The query_logs ``created_at`` column is stored as an ISO 8601 string.
    A pure lexical comparison is therefore sufficient because the column
    is consistently formatted as ``YYYY-MM-DDTHH:MM:SS[+00:00]`` by
    :func:`app.services.retrieval.retrieve`.
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be > 0")
    anchor = now or datetime.now(timezone.utc)
    return (anchor - timedelta(days=retention_days)).isoformat(timespec="seconds")


def prune_query_logs(retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Delete QueryLog rows older than ``retention_days``.

    Returns the number of deleted rows.  The function does not touch
    :class:`QueryFeedback`, which is allowed to outlive the originating
    query log because aggregate feedback is referenced from external
    dashboards.
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be > 0")
    cutoff = _cutoff(retention_days)
    with SessionLocal() as db:
        try:
            result = db.execute(
                delete(QueryLog).where(QueryLog.created_at < cutoff),
            )
            db.commit()
            deleted = int(result.rowcount or 0)
        except Exception:
            # Make the cleanup atomic and visible.  Session.close() rolls back
            # in most SQLAlchemy configurations, but an explicit rollback is
            # required for callers that reuse a session/connection wrapper.
            db.rollback()
            logger.exception(
                "prune_query_logs failed; transaction rolled back (cutoff=%s)",
                cutoff,
            )
            raise
    logger.info(
        "prune_query_logs removed %d rows older than %s (retention=%dd)",
        deleted, cutoff, retention_days,
    )
    return deleted


def count_expired_query_logs(retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Return the number of QueryLog rows that would be deleted today.

    This is intended for observability dashboards and tests, and never
    mutates the database.
    """
    cutoff = _cutoff(retention_days)
    with SessionLocal() as db:
        return int(
            db.query(QueryLog).filter(QueryLog.created_at < cutoff).count(),
        )


def prune_query_logs_cli(argv: list[str] | None = None) -> int:
    """Entry point for the ``prune-query-logs`` command-line tool."""
    parser = argparse.ArgumentParser(
        description="Prune QueryLog rows older than the retention window.",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_RETENTION_DAYS,
        help="Retention window in days (default: 30)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Only print how many rows would be removed; do not delete.",
    )
    args = parser.parse_args(argv)

    if args.days <= 0:
        print("--days must be > 0", file=sys.stderr)
        return 2

    expired = count_expired_query_logs(args.days)
    print(
        f"[prune-query-logs] retention={args.days}d cutoff={_cutoff(args.days)} "
        f"expired_rows={expired}"
    )
    if args.dry_run:
        print("[prune-query-logs] dry-run, no rows removed")
        return 0

    deleted = prune_query_logs(args.days)
    print(f"[prune-query-logs] removed {deleted} rows")
    return 0


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "count_expired_query_logs",
    "prune_query_logs",
    "prune_query_logs_cli",
]


if __name__ == "__main__":  # pragma: no cover - exercised by operators
    raise SystemExit(prune_query_logs_cli())
