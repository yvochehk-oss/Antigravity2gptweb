"""Async job processing with retry mechanism.

Supports exponential backoff for failed jobs.
"""
import threading
import time
from datetime import datetime, timezone
from sqlalchemy import select
from ..db import SessionLocal
from ..models import IngestJob, Document
from ..config import WORKER_POLL_SECONDS, PROCESS_JOBS_INLINE, MAX_JOB_RETRIES, JOB_RETRY_BACKOFF_SECONDS
from ..logging_config import get_logger
from .ingest import parse_and_index

logger = get_logger(__name__)

_stop = threading.Event()
_thread = None


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
    backoff_seconds = JOB_RETRY_BACKOFF_SECONDS * (2 ** attempts)
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
            IngestJob.document_id == document_id,
            IngestJob.status.in_(["QUEUED", "RUNNING", "RETRY"])
        )
    )
    if existing:
        return existing

    doc = db.get(Document, document_id)
    if doc and not doc.duplicate_of_id:
        doc.parse_status = "QUEUED"
        doc.parse_attempts = 0

    job = IngestJob(
        document_id=document_id,
        status="QUEUED",
        created_at=now(),
        max_attempts=MAX_JOB_RETRIES
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(f"Enqueued parse job {job.id} for document {document_id}")

    if PROCESS_JOBS_INLINE:
        process_job(job.id)
        db.refresh(job)

    return job


def process_job(job_id: int) -> bool:
    """Process a single ingest job with retry support.

    Args:
        job_id: Job ID to process

    Returns:
        True if job completed successfully, False otherwise
    """
    db = SessionLocal()
    job = db.get(IngestJob, job_id)

    if not job or job.status not in ("QUEUED", "RETRY"):
        db.close()
        return False

    # Check if job should wait for retry backoff
    if job.status == "RETRY" and job.next_retry_at:
        next_retry = datetime.fromisoformat(job.next_retry_at.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) < next_retry:
            db.close()
            return False

    job.status = "RUNNING"
    job.started_at = now()
    job.attempts += 1
    db.commit()

    logger.info(f"Processing job {job_id} (attempt {job.attempts}/{job.max_attempts})")

    try:
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
            logger.info(f"Job {job_id} completed successfully: {doc.parse_message}")
        else:
            raise RuntimeError(doc.parse_message or "Parsing did not result in INDEXED status")

        job.finished_at = now()
        db.commit()
        db.close()
        return True

    except Exception as e:
        error_msg = str(e)
        job.last_error = error_msg
        logger.error(f"Job {job_id} failed: {error_msg}")

        # Check if we should retry
        if job.attempts < job.max_attempts:
            job.status = "RETRY"
            job.next_retry_at = _calculate_next_retry(job.attempts)
            job.message = f"Retry scheduled (attempt {job.attempts}/{job.max_attempts})"
            logger.info(
                f"Job {job_id} scheduled for retry at {job.next_retry_at}"
            )
        else:
            job.status = "FAILED"
            job.message = f"Max retries ({job.max_attempts}) exceeded"

            # Update document status
            doc = db.get(Document, job.document_id)
            if doc:
                doc.parse_status = "PARSE_FAILED"
                doc.parse_message = f"Max retries exceeded: {error_msg}"

            logger.warning(
                f"Job {job_id} permanently failed after {job.attempts} attempts"
            )

        job.finished_at = now()
        db.commit()
        db.close()
        return False


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
        .where(IngestJob.status == "RETRY")
        .order_by(IngestJob.next_retry_at.asc())
        .limit(1)
    )

    # If no ready retry jobs, get a new QUEUED job
    if not job:
        job = db.scalar(
            select(IngestJob)
            .where(IngestJob.status == "QUEUED")
            .order_by(IngestJob.id.asc())
            .limit(1)
        )

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
        did = process_next()
        if not did:
            _stop.wait(WORKER_POLL_SECONDS)
    logger.info("Ingest worker stopped")


def start_worker():
    """Start the background worker thread."""
    global _thread
    if _thread and _thread.is_alive():
        logger.warning("Worker already running, ignoring start request")
        return

    _stop.clear()
    _thread = threading.Thread(target=_loop, name="projectrag-ingest-worker", daemon=True)
    _thread.start()
    logger.info("Background worker thread started")


def stop_worker():
    """Stop the background worker thread gracefully."""
    logger.info("Stopping worker...")
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
    logger.info("Worker stopped")


def get_worker_status() -> dict:
    """Get current worker status for monitoring.

    Returns:
        Dict with worker state information
    """
    return {
        "running": _thread is not None and _thread.is_alive(),
        "thread_id": _thread.ident if _thread else None,
        "thread_name": _thread.name if _thread else None,
    }
