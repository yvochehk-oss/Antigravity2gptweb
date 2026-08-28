"""Regression tests for the dashboard's MinerU job monitor contract."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Job:
    id: int = 17
    document_id: int = 9
    attempts: int = 2
    started_at: str = "2026-08-27T08:00:00+00:00"


@dataclass
class _Document:
    filename: str = "实际文件名.pdf"


@dataclass
class _Project:
    id: int = 1
    project_code: str = "P-1"
    name: str = "真实项目"
    entity_code: str = ""


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return _ScalarResult([
            row[0] if isinstance(row, (tuple, list)) else row
            for row in self._rows
        ])


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _Session:
    def __init__(
        self,
        counts,
        active_job=None,
        document_rows=None,
        chunk_count=0,
        projects=None,
        entity_values=None,
        counterparty_values=None,
        project_document_counts=None,
        project_indexed_counts=None,
        project_stats_rows=None,
    ):
        self.counts = counts
        self.active_job = active_job
        self.document_rows = document_rows
        self.chunk_count = chunk_count
        self.projects = projects or []
        self.entity_values = entity_values or []
        self.counterparty_values = counterparty_values or []
        self.project_document_counts = project_document_counts or {}
        self.project_indexed_counts = project_indexed_counts or {}
        self.project_stats_rows = project_stats_rows or []

    def execute(self, statement):
        sql = str(statement).lower()
        if "from ingest_jobs" in sql:
            return _Result(self.counts.items())
        if "group by documents.project_id" in sql:
            return _Result(self.project_stats_rows)
        if "documents.parse_status" in sql:
            return _Result(self.document_rows or [])
        if "documents.entity_code" in sql:
            return _Result([(value,) for value in self.entity_values])
        if "documents.counterparty_code" in sql:
            return _Result([(value,) for value in self.counterparty_values])
        if "from projects" in sql:
            return _Result(self.projects)
        return _Result([])

    def scalar(self, statement):
        sql = str(statement).lower()
        if "from ingest_jobs" in sql:
            return self.active_job
        if "from chunks" in sql:
            return self.chunk_count
        if "count(documents.id)" in sql:
            project_id = next(iter(self.project_document_counts), 1)
            if "parse_status" in sql:
                return self.project_indexed_counts.get(project_id, 0)
            return self.project_document_counts.get(project_id, 0)
        return 0

    def get(self, model, identifier):
        if model.__name__ == "Document":
            assert identifier == 9
            return _Document()
        return next((project for project in self.projects if project.id == identifier), None)


class _DbContext:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, traceback):
        return False


@pytest.fixture
def main_module(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    from app import main

    return main


def test_active_route_precedes_integer_job_route(main_module):
    routes = [route.path for route in main_module.app.routes if hasattr(route, "path")]
    assert routes.index("/api/v1/jobs/active") < routes.index("/api/v1/jobs/{job_id}")


@pytest.mark.parametrize(
    ("counts", "active_job", "expected_status"),
    [
        ({"RUNNING": 0, "QUEUED": 3, "RETRY": 2, "FAILED": 1}, None, "queued"),
        ({"RUNNING": 0, "QUEUED": 0, "RETRY": 2, "FAILED": 1}, None, "retry"),
        ({"RUNNING": 0, "QUEUED": 0, "RETRY": 0, "FAILED": 1}, None, "failed"),
        ({"RUNNING": 0, "QUEUED": 0, "RETRY": 0, "FAILED": 0}, None, "idle"),
    ],
)
def test_monitor_distinguishes_non_idle_queue_states(
    main_module, counts, active_job, expected_status
):
    payload = main_module._job_monitor_snapshot(_Session(counts, active_job))
    assert payload["status"] == expected_status
    assert payload["active"] is False
    assert payload["counts"] == {
        "active": counts["RUNNING"],
        "queued": counts["QUEUED"],
        "retry": counts["RETRY"],
        "failed": counts["FAILED"],
    }


def test_monitor_uses_real_document_and_job_timestamps(main_module):
    payload = main_module._job_monitor_snapshot(
        _Session(
            {"RUNNING": 1, "QUEUED": 10, "RETRY": 2, "FAILED": 3},
            _Job(),
        )
    )
    assert payload["status"] == "active"
    assert payload["active"] is True
    assert payload["document_name"] == "实际文件名.pdf"
    assert payload["started_at"] == "2026-08-27T08:00:00+00:00"
    assert payload["active_job"]["document_name"] == "实际文件名.pdf"
    assert payload["counts"] == {"active": 1, "queued": 10, "retry": 2, "failed": 3}


def test_monitor_exposes_document_and_chunk_kpis_with_document_waiting_semantics(main_module):
    payload = main_module._job_monitor_snapshot(
        _Session(
            {"RUNNING": 0, "QUEUED": 2, "RETRY": 1, "FAILED": 0},
                document_rows=[
                ("INDEXED", 28),
                ("PARSE_FAILED", 114),
                ("QUEUED", 464),
                ("WAITING_MINERU", 2),
                ("UPLOADED", 1),
                ],
                chunk_count=74,
                project_stats_rows=[(1, 581, 28)],
            )
    )
    assert payload["document_total"] == 609
    assert payload["indexed_documents"] == 28
    assert payload["indexed_chunks"] == 74
    assert payload["waiting_documents"] == 581
    assert payload["projects_stats"] == {1: {"doc_count": 581, "indexed_count": 28}}
    for stats in payload["projects_stats"].values():
        assert set(stats) == {"doc_count", "indexed_count"}
        assert all(isinstance(value, int) and value >= 0 for value in stats.values())


@pytest.mark.parametrize("bad_count", [-1, 1.5, "2", True])
def test_monitor_rejects_invalid_kpi_counts(main_module, bad_count):
    session = _Session(
        {"RUNNING": 0, "QUEUED": 0, "RETRY": 0, "FAILED": 0},
        document_rows=[("INDEXED", bad_count)],
    )
    with pytest.raises(ValueError, match="non-negative integer"):
        main_module._document_monitor_kpis(session)


def test_dashboard_project_stats_contract_is_non_negative_and_balanced(main_module, monkeypatch):
    session = _Session(
        {"RUNNING": 0, "QUEUED": 0, "RETRY": 0, "FAILED": 0},
        document_rows=[("INDEXED", 2), ("QUEUED", 1)],
        chunk_count=3,
        projects=[_Project()],
        entity_values=["E-1", "EXT-1"],
        counterparty_values=["EXT-2"],
        project_document_counts={1: 3},
        project_indexed_counts={1: 2},
    )
    captured = {}

    monkeypatch.setattr(main_module, "get_db", lambda: _DbContext(session))
    monkeypatch.setattr(main_module, "_canonical_entity_views", lambda db: [])
    monkeypatch.setattr(main_module, "mineru_available", lambda: False)
    monkeypatch.setattr(main_module, "get_worker_status", lambda: {})
    monkeypatch.setattr(
        main_module.templates,
        "TemplateResponse",
        lambda request, template_name, context: captured.update(context) or context,
    )

    main_module.dashboard(object(), principal=object())
    stats = captured["project_stats"][1]
    for key in ("doc_count", "indexed_count", "entity_count", "internal_count", "external_count"):
        assert isinstance(stats[key], int)
        assert stats[key] >= 0
    assert stats["doc_count"] == 3
    assert stats["indexed_count"] == 2
    assert stats["entity_count"] == len(stats["entities"])
    assert stats["internal_count"] + stats["external_count"] == stats["entity_count"]


def test_monitor_api_returns_json_instead_of_integer_path_validation_error(
    main_module, monkeypatch
):
    from app.auth import require_web_or_service_read

    session = _Session(
        {"RUNNING": 0, "QUEUED": 3, "RETRY": 2, "FAILED": 1},
        None,
    )
    monkeypatch.setattr(main_module, "get_db", lambda: _DbContext(session))
    main_module.app.dependency_overrides[require_web_or_service_read] = lambda: object()
    try:
        response = TestClient(main_module.app).get("/api/v1/jobs/active")
    finally:
        main_module.app.dependency_overrides.pop(require_web_or_service_read, None)

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["counts"]["queued"] == 3
    assert response.json()["document_total"] == 0
    assert response.json()["indexed_documents"] == 0
    assert response.json()["indexed_chunks"] == 0
    assert response.json()["waiting_documents"] == 0


def test_dashboard_checks_http_and_contract_errors_before_idle(main_module):
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    fetch_start = template.index("fetch('/api/v1/jobs/active'")
    monitor_script = template[fetch_start: template.index("</script>", fetch_start)]
    assert "if (!res.ok)" in monitor_script
    assert "invalid monitor response" in monitor_script
    assert "监控异常：无法读取后台任务状态。" in monitor_script
    assert "data.counts.queued + data.counts.retry" in monitor_script
    assert "data.counts.failed > 0 && data.status !== 'idle'" in monitor_script
    assert "历史失败 ${data.counts.failed} 项" in monitor_script
    assert "另有 ${data.counts.failed} 个历史失败任务待查看。" in monitor_script
    assert "当前无待解析任务" in monitor_script
    assert "kpi-indexed-chunks" in template
    assert "kpi-waiting-documents" in template
    assert "Number.isInteger(data[key]) && data[key] >= 0" in monitor_script
    assert "data.indexed_chunks.toLocaleString('zh-CN')" in monitor_script
    assert "data.waiting_documents.toLocaleString('zh-CN')" in monitor_script
