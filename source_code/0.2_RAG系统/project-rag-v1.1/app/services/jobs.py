"""Async job processing with retry mechanism.

Supports exponential backoff for failed jobs.

The worker cooperates with ``SIGTERM``/``SIGINT`` so the FastAPI
``lifespan`` shutdown (and external process supervisors) can stop it
without leaving a job half-processed.  ``_stop`` is also exposed through
:func:`request_stop` so tests and CLI tools can drive a graceful exit
without raising signals from a worker thread.
"""

import signal
import threading
from datetime import datetime, timezone

from sqlalchemy import or_, select

from ..config import (
    JOB_RETRY_BACKOFF_SECONDS,
    MAX_JOB_RETRIES,
    PROCESS_JOBS_INLINE,
    WORKER_POLL_SECONDS,
    WORKER_SHUTDOWN_TIMEOUT_SECONDS,
)
from ..db import SessionLocal
from ..logging_config import get_logger
from ..models import Document, IngestJob
from ..observability import get_request_id, request_scope
from .ingest import parse_and_index

logger = get_logger(__name__)

_stop = threading.Event()
_threads: list[threading.Thread] = []       # 多 worker 并发列表
_shutdown_timed_out = False
_signal_handlers_installed = False
_signal_handlers_lock = threading.Lock()
_worker_start_lock = threading.Lock()
_active_job_ids: set[int] = set()
_active_job_ids_lock = threading.Lock()


def now() -> str:
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _calculate_next_retry(attempts: int) -> str:
    """Calculate next retry time with exponential backoff.

    Args:
        attempts: Current attempt number

    Returns:
        ISO timestamp for next retry
    """
    backoff_seconds = JOB_RETRY_BACKOFF_SECONDS * (2**attempts)
    next_time = datetime.now(timezone.utc).timestamp() + backoff_seconds
    return datetime.fromtimestamp(next_time, tz=timezone.utc).isoformat(timespec="seconds")


def enqueue_parse(db, document_id: int) -> IngestJob:
    """Create a new parse job for a document.

    Prevents duplicate QUEUED/RUNNING jobs for the same document.

    Args:
        db: Database session
        document_id: Document to parse

    Returns:
        Created or existing IngestJob
    """
    existing = db.scalar(
        select(IngestJob).where(
            IngestJob.document_id == document_id, IngestJob.status.in_(["QUEUED", "RUNNING", "RETRY"])
        )
    )
    if existing:
        return existing

    doc = db.get(Document, document_id)
    if doc and not doc.duplicate_of_id:
        doc.parse_status = "QUEUED"
        doc.parse_attempts = 0

    job = IngestJob(document_id=document_id, status="QUEUED", created_at=now(), max_attempts=MAX_JOB_RETRIES)
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(f"Enqueued parse job {job.id} for document {document_id}")

    if PROCESS_JOBS_INLINE:
        process_job(job.id)
        db.refresh(job)

    return job


def recover_stale_running_jobs() -> int:
    """Recover jobs left in ``RUNNING`` by an earlier worker process.

    This is deliberately called once, immediately before a worker thread is
    started.  Row locks and one transaction make the job/document transition
    atomic on PostgreSQL.  Jobs currently executing in this process are
    excluded using ``_active_job_ids``; a second ``start_worker`` call while
    the worker is alive is rejected before this helper is reached.

    Returns:
        Number of abandoned jobs transitioned to ``RETRY`` or ``FAILED``.
    """
    recovered = 0
    db = SessionLocal()
    try:
        # Serialise the in-process active-job snapshot with process_job's
        # claim.  The database row lock then serialises this recovery against
        # another worker/process touching the same job.
        with _active_job_ids_lock:
            active_job_ids = set(_active_job_ids)
            with db.begin():
                stale_jobs = db.scalars(
                    select(IngestJob)
                    .where(IngestJob.status == "RUNNING")
                    .with_for_update()
                ).all()

                recovered_at = now()
                for job in stale_jobs:
                    if job.id in active_job_ids:
                        logger.info(
                            "Keeping active ingest job %s in RUNNING during startup recovery",
                            job.id,
                        )
                        continue

                    try:
                        attempts = int(job.attempts)
                        max_attempts = int(job.max_attempts)
                    except (TypeError, ValueError):
                        attempts = -1
                        max_attempts = 0

                    # Invalid counters are terminal rather than silently
                    # granting retries.  This is the fail-closed branch.
                    retryable = 0 <= attempts < max_attempts and max_attempts > 0
                    if retryable:
                        job.status = "RETRY"
                        job.next_retry_at = _calculate_next_retry(attempts)
                        job.message = (
                            "Recovered abandoned RUNNING job; retry scheduled "
                            f"(attempt {attempts}/{max_attempts})"
                        )
                        document_status = "QUEUED"
                        document_message = job.message
                        logger.warning(
                            "Recovered abandoned ingest job %s as RETRY (attempt %s/%s)",
                            job.id,
                            attempts,
                            max_attempts,
                        )
                    else:
                        job.status = "FAILED"
                        job.next_retry_at = ""
                        job.message = (
                            "Recovered abandoned RUNNING job as FAILED; "
                            f"max retries ({max_attempts}) exceeded"
                        )
                        document_status = "PARSE_FAILED"
                        document_message = job.message
                        logger.error(
                            "Recovered abandoned ingest job %s as FAILED (attempts=%s, max_attempts=%s)",
                            job.id,
                            attempts,
                            max_attempts,
                        )

                    job.finished_at = recovered_at
                    if not job.last_error:
                        job.last_error = "Worker process exited while job was RUNNING"

                    document = db.get(Document, job.document_id)
                    if document:
                        document.parse_status = document_status
                        document.parse_attempts = max(attempts, 0)
                        document.parse_message = document_message
                    recovered += 1
        logger.info("Startup ingest recovery completed: %s job(s) recovered", recovered)
        return recovered
    except Exception:
        logger.exception("Startup ingest recovery failed; worker will not start")
        raise
    finally:
        db.close()


def process_job(job_id: int) -> bool:
    """Process a single ingest job with retry support.

    Args:
        job_id: Job ID to process

    Returns:
        True if job completed successfully, False otherwise
    """
    # Claim the id before opening the session so startup recovery cannot
    # mistake a synchronous job in this process for an abandoned job.
    with _active_job_ids_lock:
        if job_id in _active_job_ids:
            logger.warning("Job %s is already active, ignoring duplicate process request", job_id)
            return False
        _active_job_ids.add(job_id)

    db = None
    job = None
    try:
        db = SessionLocal()
        job = db.get(IngestJob, job_id)

        if not job or job.status not in ("QUEUED", "RETRY"):
            return False

        # Check if job should wait for retry backoff
        if job.status == "RETRY" and job.next_retry_at:
            next_retry = datetime.fromisoformat(job.next_retry_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < next_retry:
                return False

        job.status = "RUNNING"
        job.started_at = now()
        job.attempts += 1
        db.commit()

        logger.info(f"Processing job {job_id} (attempt {job.attempts}/{job.max_attempts})")

        doc = db.get(Document, job.document_id)
        if not doc:
            raise RuntimeError(f"Document {job.document_id} not found")

        # Update document parse attempts
        doc.parse_attempts = job.attempts
        db.commit()

        parse_and_index(db, doc)

        if doc.parse_status == "INDEXED":
            job.status = "COMPLETED"
            job.message = doc.parse_message
            # A successful retry supersedes the prior failure/backoff state.
            # These columns are NOT NULL in the runtime schema, so use the
            # contract's empty-string sentinel rather than assigning None.
            job.last_error = ""
            job.next_retry_at = ""
            logger.info(f"Job {job_id} completed successfully: {doc.parse_message}")
        else:
            raise RuntimeError(doc.parse_message or "Parsing did not result in INDEXED status")

        job.finished_at = now()
        db.commit()
        return True

    except Exception as e:
        if db is None or job is None:
            if db is not None:
                db.rollback()
            raise
        db.rollback()
        error_msg = str(e).replace("\\x00", "")
        job.last_error = error_msg
        logger.error(f"Job {job_id} failed: {error_msg}")

        # Check if we should retry
        if job.attempts < job.max_attempts:
            job.status = "RETRY"
            job.next_retry_at = _calculate_next_retry(job.attempts)
            job.message = f"Retry scheduled (attempt {job.attempts}/{job.max_attempts})"
            logger.info(f"Job {job_id} scheduled for retry at {job.next_retry_at}")
        else:
            job.status = "FAILED"
            job.message = f"Max retries ({job.max_attempts}) exceeded"

            # Update document status
            doc = db.get(Document, job.document_id)
            if doc:
                doc.parse_status = "PARSE_FAILED"
                doc.parse_message = f"Max retries exceeded: {error_msg}"

            logger.warning(f"Job {job_id} permanently failed after {job.attempts} attempts")

        job.finished_at = now()
        db.commit()
        return False
    finally:
        if db is not None:
            db.close()
        with _active_job_ids_lock:
            _active_job_ids.discard(job_id)


def process_next() -> bool:
    """Process the next available job in queue.

    Jobs are ordered by:
    1. QUEUED status first (new jobs)
    2. RETRY status if backoff time has passed

    Returns:
        True if a job was processed, False if queue was empty
    """
    db = SessionLocal()

    # First check for ready RETRY jobs
    job = db.scalar(
        select(IngestJob)
        .where(
            IngestJob.status == "RETRY",
            or_(
                IngestJob.next_retry_at.is_(None),
                IngestJob.next_retry_at == "",
                IngestJob.next_retry_at <= now(),
            ),
        )
        .order_by(IngestJob.next_retry_at.asc())
        .limit(1)
    )

    # If no ready retry jobs, get a new QUEUED job
    if not job:
        job = db.scalar(select(IngestJob).where(IngestJob.status == "QUEUED").order_by(IngestJob.id.asc()).limit(1))

    jid = job.id if job else None
    db.close()

    if not jid:
        return False

    process_job(jid)
    return True


def _loop():
    """Main worker loop."""
    logger.info("Ingest worker started")
    while not _stop.is_set():
        try:
            # A worker iteration has no inbound HTTP context.  Bind a fresh
            # trace id so background failures are still correlated in logs,
            # while preserving a caller's id when ``process_next`` is used
            # synchronously by an API/test.
            with request_scope(get_request_id() or None):
                did = process_next()
        except Exception:
            # A transient DB/queue error must not silently kill the worker or
            # leave it looking healthy until the next restart.
            logger.exception("Ingest worker iteration failed")
            did = False
        if not did:
            # Wait for either the stop signal or the poll interval, whichever
            # comes first.  Using ``Event.wait`` here (instead of ``time.sleep``)
            # makes SIGTERM-style shutdowns effectively instantaneous even when
            # the queue is empty.
            if _stop.wait(WORKER_POLL_SECONDS):
                break
    logger.info("Ingest worker stopped")


def _install_signal_handlers() -> None:
    """Install ``SIGTERM``/``SIGINT`` handlers that flip the stop event.

    The handlers are installed in the *main* thread; signal delivery from
    worker threads to ``signal.signal`` is not supported in CPython.  Tests
    usually exercise :func:`request_stop` directly so they do not need to
    install handlers, and the install is idempotent.
    """
    global _signal_handlers_installed
    with _signal_handlers_lock:
        if _signal_handlers_installed:
            return

        def _handle(signum, _frame):  # pragma: no cover - relies on OS signal
            logger.info(f"Worker received signal {signum}; initiating graceful shutdown")
            request_stop()

        try:
            signal.signal(signal.SIGTERM, _handle)
        except (ValueError, OSError):
            # ``SIGTERM`` is unavailable in some test runners (and inside a
            # non-main thread).  Falling back to ``SIGINT`` keeps the behaviour
            # close enough for production.
            pass
        try:
            signal.signal(signal.SIGINT, _handle)
        except (ValueError, OSError):
            pass
        _signal_handlers_installed = True


def request_stop() -> None:
    """Flip the worker stop flag without raising a signal.

    This is the entry point tests use to assert a graceful shutdown.  It
    is also wired to the ``SIGTERM``/``SIGINT`` handlers installed by
    :func:`_install_signal_handlers` and to ``stop_worker``.
    """
    _stop.set()


def start_worker(*, install_signal_handlers: bool = True, concurrency: int | None = None):
    """Start background worker threads for parallel ingest processing.

    Args:
        install_signal_handlers: Whether to install SIGTERM/SIGINT handlers.
        concurrency: Number of parallel worker threads. Defaults to
            ``WORKER_CONCURRENCY`` from config/env.
    """
    from ..config import WORKER_CONCURRENCY as _DEFAULT_CONCURRENCY
    n = max(1, concurrency if concurrency is not None else _DEFAULT_CONCURRENCY)

    global _threads, _shutdown_timed_out
    with _worker_start_lock:
        # Remove dead threads from the list first
        _threads = [t for t in _threads if t.is_alive()]
        running = len(_threads)
        if running >= n:
            logger.warning(
                "Worker already running with %d/%d threads, ignoring start request",
                running, n,
            )
            return

        # Recovery only on fresh start (no live workers yet)
        if running == 0:
            recover_stale_running_jobs()
            _stop.clear()
            _shutdown_timed_out = False

        # Spin up the missing slots
        to_add = n - running
        for i in range(to_add):
            t = threading.Thread(
                target=_loop,
                name=f"projectrag-ingest-worker-{running + i + 1}",
                daemon=True,
            )
            t.start()
            _threads.append(t)

        if install_signal_handlers:
            _install_signal_handlers()
        logger.info("Background worker threads started (%d/%d active)", len(_threads), n)


def stop_worker():
    """Stop all background worker threads gracefully."""
    global _shutdown_timed_out
    logger.info("Stopping worker...")
    request_stop()
    timed_out = False
    for thread in list(_threads):
        if thread and thread is not threading.current_thread():
            thread.join(timeout=WORKER_SHUTDOWN_TIMEOUT_SECONDS)
            if thread.is_alive():
                timed_out = True
    if timed_out:
        _shutdown_timed_out = True
        logger.error(
            "Worker did not stop within %.1fs; shutdown is degraded",
            WORKER_SHUTDOWN_TIMEOUT_SECONDS,
        )
        return False
    logger.info("Worker stopped")
    return True


def get_worker_status() -> dict:
    """Get current worker status for monitoring."""
    live = [t for t in _threads if t.is_alive()]
    return {
        "running": len(live) > 0,
        "concurrency": len(live),
        "thread_names": [t.name for t in live],
        "stop_requested": _stop.is_set(),
        "shutdown_timed_out": _shutdown_timed_out,
    }
