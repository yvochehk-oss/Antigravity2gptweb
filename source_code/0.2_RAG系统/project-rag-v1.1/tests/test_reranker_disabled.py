from __future__ import annotations

from app.services import reranker


def test_disabled_reranker_is_passthrough_without_model_load(monkeypatch):
    monkeypatch.setattr(reranker, "RERANKER_ENABLED", False)
    monkeypatch.setattr(reranker, "RERANKER_BACKEND", "bge_v2_m3")

    def fail_if_model_loads():
        raise AssertionError("disabled reranker must not load torch/transformers model")

    monkeypatch.setattr(reranker, "_get_model", fail_if_model_loads)

    rows = [
        {"chunk_id": 1, "text": "第一条"},
        {"chunk_id": 2, "text": "第二条"},
    ]

    result = reranker.rerank("测试查询", rows, top_k=1)

    assert result == rows[:1]


def test_disabled_reranker_runtime_reports_no_loaded_model(monkeypatch):
    monkeypatch.setattr(reranker, "RERANKER_ENABLED", False)
    monkeypatch.setattr(reranker, "RERANKER_BACKEND", "bge_v2_m3")

    runtime = reranker.reranker_runtime()

    assert runtime["enabled"] is False
    assert runtime["model"] == ""
    assert runtime["model_loaded"] is False
