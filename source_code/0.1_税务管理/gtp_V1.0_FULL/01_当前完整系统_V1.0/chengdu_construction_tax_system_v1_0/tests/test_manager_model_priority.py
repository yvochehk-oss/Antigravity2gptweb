"""Contracts for the manager project's automatic AI model selection."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).parents[1]


def _endpoint(
    name: str,
    endpoint_id: int,
    priority: int,
    *,
    group: str = "default",
    adapter: str = "openai_compatible",
    enabled: bool = True,
):
    return SimpleNamespace(
        id=endpoint_id,
        name=name,
        adapter=adapter,
        enabled=enabled,
        priority=priority,
        routing_group=group,
    )


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *_args, **_kwargs):
        return _Query(self.rows)


def test_manager_endpoint_list_uses_group_priority_id_and_excludes_mock():
    from app.routers import manager

    rows = [
        _endpoint("other-group", 8, 1, group="other"),
        _endpoint("second", 2, 200),
        _endpoint("first", 1, 100),
        _endpoint("legacy-mock", 3, 1, adapter="mock"),
        _endpoint("disabled", 4, 50, enabled=False),
    ]

    selected = manager._manager_ai_endpoints(_Session(rows))

    assert [(row.id, row.routing_group, row.priority) for row in selected] == [
        (1, "default", 100),
        (2, "default", 200),
        (8, "other", 1),
    ]
    assert all(str(row.adapter).lower() != "mock" for row in selected)


def test_default_failover_pool_starts_with_first_configured_model():
    from app.ai.failover import select_failover_endpoints

    first = _endpoint("first", 1, 100)
    second = _endpoint("second", 2, 200)
    other_group = _endpoint("other", 3, 1, group="other")
    selected = select_failover_endpoints(
        None,
        endpoint_id=None,
        endpoints=[second, other_group, first],
    )

    assert [row.id for row in selected] == [1, 2]


def test_manager_endpoint_order_preserves_zero_priority_before_fifty():
    from app.routers import manager

    zero = _endpoint("zero", 9, 0)
    fifty = _endpoint("fifty", 10, 50)

    selected = manager._manager_ai_endpoints(_Session([fifty, zero]))

    assert [(row.name, row.priority) for row in selected] == [
        ("zero", 0),
        ("fifty", 50),
    ]


def test_manager_project_template_has_explicit_auto_mode_without_forced_id():
    source = (ROOT / "app" / "templates" / "manager_project.html").read_text(
        encoding="utf-8",
    )

    assert 'value="" selected>自动选择（按模型设置顺序）' in source
    assert "loop.first" not in source
    assert "if (endpointId) formData.endpoint_id = endpointId;" in source
    assert "new URLSearchParams({question: q, endpoint_id: endpointId})" not in source
