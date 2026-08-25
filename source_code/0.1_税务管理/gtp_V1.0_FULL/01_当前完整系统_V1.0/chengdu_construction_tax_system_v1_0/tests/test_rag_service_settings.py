"""Integration coverage for administrator-managed Tax -> RAG endpoints."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": "TestPass12345!"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_rag_settings_are_admin_only_persisted_and_credential_free(seeded_app, monkeypatch):
    from app.db import SessionLocal
    from app.main import app
    from app.models import RagServiceEndpoint
    from app.routers import rag_sync

    # Keep this singleton test deterministic when the disposable DB is reused
    # for a focused test run.
    db = SessionLocal()
    db.query(RagServiceEndpoint).delete()
    db.commit()
    db.close()

    def fake_probe(raw_url: str, **kwargs):
        assert kwargs.get("explicit_private_approval") is True
        return (
            rag_sync.RagConnectResponse(
                ok=True,
                rag_version="1.1-test",
                llm_extraction=True,
                projects=[{"id": 7, "project_code": "R-7", "name": "RAG 项目"}],
            ),
            frozenset({"192.168.1.20"}),
            raw_url.rstrip("/"),
        )

    monkeypatch.setattr(rag_sync, "_probe_rag", fake_probe)
    try:
        anonymous = TestClient(app)
        assert anonymous.get("/rag-sync/settings").status_code == 401

        operator = TestClient(app)
        _login(operator, "operator")
        assert operator.post(
            "/rag-sync/settings/test",
            json={"url": "http://192.168.1.20:8922", "approve_private": True},
        ).status_code == 403
        # A normal authenticated user may read non-secret metadata, but may
        # not test or change the outbound destination.
        assert operator.get("/rag-sync/settings").status_code == 200

        admin = TestClient(app)
        _login(admin, "admin")
        tested = admin.post(
            "/rag-sync/settings/test",
            json={"url": "http://192.168.1.20:8922", "approve_private": True},
        )
        assert tested.status_code == 200
        assert tested.json()["ok"] is True
        assert "rag-test-secret-at-least-32-characters" not in tested.text

        saved = admin.post(
            "/rag-sync/settings",
            json={"url": "http://192.168.1.20:8922", "approve_private": True},
        )
        assert saved.status_code == 200
        saved_payload = saved.json()
        assert saved_payload["configured"] is True
        assert saved_payload["approved_private"] is True
        assert "api_key" not in saved.text.lower()

        db = SessionLocal()
        endpoint = db.get(RagServiceEndpoint, 1)
        assert endpoint is not None
        assert endpoint.base_url == "http://192.168.1.20:8922"
        assert endpoint.resolved_addresses_json == '["192.168.1.20"]'
        assert endpoint.approved_by == "admin"
        db.close()

        # Facts Provider requests use the same persisted endpoint, rather than
        # reverting to the process-start ``TAX_RAG_V1_FACTS_URL`` value.
        from app.services.facts_client import _configured_facts_base_url

        db = SessionLocal()
        assert _configured_facts_base_url(db) == "http://192.168.1.20:8922"
        db.close()

        visible = operator.get("/rag-sync/settings")
        assert visible.status_code == 200
        assert visible.json()["host"] == "192.168.1.20"
        assert "rag-test-secret-at-least-32-characters" not in visible.text
    finally:
        db = SessionLocal()
        db.query(RagServiceEndpoint).delete()
        db.commit()
        db.close()
