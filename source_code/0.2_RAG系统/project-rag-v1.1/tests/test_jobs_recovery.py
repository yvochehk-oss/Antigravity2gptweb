"""Unit coverage for ingest-worker startup recovery."""

from dataclasses import dataclass

import pytest

from app.models import Document, IngestJob
from app.services import jobs as jobs_service


@dataclass
class _FakeDocument:
    id: int
    parse_status: str = "PARSING"
    parse_attempts: int = 0
    parse_message: str = ""


@dataclass
class _FakeJob:
    id: int
    document_id: int
    status: str = "RUNNING"
    attempts: object = 0
    max_attempts: object = 3
    message: str = ""
    last_error: str = ""
    started_at: str = "2026-08-26T01:00:00+00:00"
    finished_at: str = ""
    next_retry_at: str = ""


class _FakeTransaction:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _tb):
        self.session.committed = exc_type is None
        return False


class _FakeResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _FakeSession:
    def __init__(self, jobs, documents):
        self.jobs = jobs
        self.documents = documents
        self.committed = False
        self.commit_count = 0
        self.closed = False

    def begin(self):
        return _FakeTransaction(self)

    def scalars(self, _statement):
        return _FakeResult(self.jobs)

    def get(self, model, object_id):
        if model is Document:
            return self.documents.get(object_id)
        if model is IngestJob:
            return next((job for job in self.jobs if job.id == object_id), None)
        raise AssertionError(f"unexpected model: {model}")

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_recovery_transitions_retry_and_terminal_jobs_with_documents(monkeypatch):
    retry_job = _FakeJob(id=1, document_id=11, attempts=1, max_attempts=3)
    failed_job = _FakeJob(id=2, document_id=12, attempts=3, max_attempts=3)
    retry_doc = _FakeDocument(id=11)
    failed_doc = _FakeDocument(id=12)
    session = _FakeSession([retry_job, failed_job], {11: retry_doc, 12: failed_doc})
    monkeypatch.setattr(jobs_service, "SessionLocal", lambda: session)
    monkeypatch.setattr(jobs_service, "now", lambda: "2026-08-27T08:00:00+00:00")
    monkeypatch.setattr(jobs_service, "_calculate_next_retry", lambda attempts: f"retry-{attempts}")

    assert jobs_service.recover_stale_running_jobs() == 2

    assert retry_job.status == "RETRY"
    assert retry_job.next_retry_at == "retry-1"
    assert retry_job.finished_at == "2026-08-27T08:00:00+00:00"
    assert retry_doc.parse_status == "QUEUED"
    assert retry_doc.parse_attempts == 1
    assert "retry scheduled" in retry_doc.parse_message

    assert failed_job.status == "FAILED"
    assert failed_job.next_retry_at in ("", None)
    assert failed_job.finished_at == "2026-08-27T08:00:00+00:00"
    assert failed_doc.parse_status == "PARSE_FAILED"
    assert failed_doc.parse_attempts == 3
    assert "max retries" in failed_doc.parse_message
    assert session.committed is True
    assert session.closed is True


def test_recovery_fails_closed_for_invalid_retry_counters(monkeypatch):
    job = _FakeJob(id=3, document_id=13, attempts="not-a-number", max_attempts=3)
    document = _FakeDocument(id=13)
    session = _FakeSession([job], {13: document})
    monkeypatch.setattr(jobs_service, "SessionLocal", lambda: session)

    assert jobs_service.recover_stale_running_jobs() == 1
    assert job.status == "FAILED"
    assert document.parse_status == "PARSE_FAILED"
    assert job.next_retry_at in ("", None)


def test_retry_job_success_clears_prior_error_and_retry_schedule(monkeypatch):
    job = _FakeJob(
        id=5,
        document_id=15,
        status="RETRY",
        attempts=1,
        max_attempts=3,
        last_error="NameError: stale worker failure",
        next_retry_at="2000-01-01T00:00:00+00:00",
    )
    document = _FakeDocument(id=15, parse_status="PARSING")
    session = _FakeSession([job], {15: document})
    monkeypatch.setattr(jobs_service, "SessionLocal", lambda: session)
    monkeypatch.setattr(jobs_service, "now", lambda: "2026-08-27T08:05:00+00:00")

    def _successful_parse(_db, doc):
        doc.parse_status = "INDEXED"
        doc.parse_message = "Indexed successfully"

    monkeypatch.setattr(jobs_service, "parse_and_index", _successful_parse)

    assert jobs_service.process_job(job.id) is True

    assert job.status == "COMPLETED"
    assert job.last_error == ""
    assert job.next_retry_at in ("", None)
    assert job.finished_at == "2026-08-27T08:05:00+00:00"
    assert document.parse_status == "INDEXED"
    assert session.commit_count == 3
    assert session.closed is True


def test_recovery_does_not_touch_current_process_active_job(monkeypatch):
    job = _FakeJob(id=4, document_id=14, attempts=1, max_attempts=3)
    document = _FakeDocument(id=14)
    session = _FakeSession([job], {14: document})
    monkeypatch.setattr(jobs_service, "SessionLocal", lambda: session)
    jobs_service._active_job_ids.clear()
    jobs_service._active_job_ids.add(job.id)
    try:
        assert jobs_service.recover_stale_running_jobs() == 0
        assert job.status == "RUNNING"
        assert document.parse_status == "PARSING"
        assert session.committed is True
    finally:
        jobs_service._active_job_ids.discard(job.id)


def test_process_job_cleans_active_claim_when_session_creation_fails(monkeypatch):
    jobs_service._active_job_ids.clear()

    def _raise_session_error():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(jobs_service, "SessionLocal", _raise_session_error)

    with pytest.raises(RuntimeError, match="database unavailable"):
        jobs_service.process_job(99)

    assert 99 not in jobs_service._active_job_ids


def test_start_worker_recovers_before_starting_threads_and_only_once(monkeypatch):
    events = []
    started_names = []

    class _FakeThread:
        def __init__(self, *, target, name, daemon):
            assert target is jobs_service._loop
            assert daemon is True
            self.name = name
            self.alive = False

        def is_alive(self):
            return self.alive

        def start(self):
            # ``start_worker`` must complete startup recovery before any
            # thread is allowed to start consuming the queue.
            assert "recovery" in events
            self.alive = True
            started_names.append(self.name)
            events.append(f"thread-start:{self.name}")

    monkeypatch.setattr(jobs_service, "_threads", [])
    monkeypatch.setattr(jobs_service.threading, "Thread", _FakeThread)
    monkeypatch.setattr(
        jobs_service,
        "_install_signal_handlers",
        lambda: events.append("signal-handlers-installed"),
    )
    recovery_calls = []

    def _recover():
        events.append("recovery")
        recovery_calls.append(1)
        return 0

    monkeypatch.setattr(
        jobs_service,
        "recover_stale_running_jobs",
        _recover,
    )

    jobs_service.start_worker(install_signal_handlers=False, concurrency=3)
    # A second start request for the already-running worker pool must not run
    # recovery again or replace any existing thread.
    jobs_service.start_worker(install_signal_handlers=False, concurrency=3)

    assert events == [
        "recovery",
        "thread-start:projectrag-ingest-worker-1",
        "thread-start:projectrag-ingest-worker-2",
        "thread-start:projectrag-ingest-worker-3",
    ]
    assert recovery_calls == [1]
    assert started_names == [
        "projectrag-ingest-worker-1",
        "projectrag-ingest-worker-2",
        "projectrag-ingest-worker-3",
    ]
    status = jobs_service.get_worker_status()
    assert status["running"] is True
    assert status["concurrency"] == 3
    assert status["thread_names"] == started_names


@pytest.mark.parametrize("requested_concurrency", [0, -2])
def test_start_worker_clamps_non_positive_concurrency_to_one(monkeypatch, requested_concurrency):
    started_names = []

    class _FakeThread:
        def __init__(self, *, target, name, daemon):
            assert target is jobs_service._loop
            assert daemon is True
            self.name = name
            self.alive = False

        def is_alive(self):
            return self.alive

        def start(self):
            self.alive = True
            started_names.append(self.name)

    monkeypatch.setattr(jobs_service, "_threads", [])
    monkeypatch.setattr(jobs_service.threading, "Thread", _FakeThread)
    recovery_calls = []

    def _recover():
        recovery_calls.append(1)
        return 0

    monkeypatch.setattr(
        jobs_service,
        "recover_stale_running_jobs",
        _recover,
    )

    jobs_service.start_worker(install_signal_handlers=False, concurrency=requested_concurrency)

    assert started_names == ["projectrag-ingest-worker-1"]
    assert recovery_calls == [1]
    assert jobs_service.get_worker_status()["concurrency"] == 1
