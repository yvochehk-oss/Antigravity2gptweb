"""V0.2 端到端 Web + AI 编排回归。"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _get_client(app) -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"username": "admin", "password": "TestPass12345!"})
    return c


def test_web_pages_all_200(seeded_app):
    from app.main import app

    c = _get_client(app)
    for path in [
        "/", "/matching", "/tax-ledger",
        "/risks", "/imports", "/audit",
        "/ai-review", "/ai-models", "/ai-prompts",
        "/health-check", "/tasks", "/healthz",
        "/docs", "/api/projects/1", "/api/projects/1/matching",
    ]:
        r = c.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_login_and_logout(seeded_app):
    from app.main import app

    c = TestClient(app)
    # 登录成功
    r_login = c.post("/login", data={"username": "admin", "password": "TestPass12345!"}, follow_redirects=False)
    assert r_login.status_code == 302
    assert "tax_session" in c.cookies

    # GET /logout 可以正常重定向至 /login 并清除 session
    r_logout_get = c.get("/logout", follow_redirects=False)
    assert r_logout_get.status_code == 302
    assert r_logout_get.headers["location"] == "/login"

    # POST /logout 同样可以正常退出
    c.post("/login", data={"username": "admin", "password": "TestPass12345!"})
    r_logout_post = c.post("/logout", follow_redirects=False)
    assert r_logout_post.status_code == 302
    assert r_logout_post.headers["location"] == "/login"



def test_ai_review_run_and_detail(seeded_app, monkeypatch):
    from app.main import app

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    c = _get_client(app)

    # 找启用的 mock 端点进行快速确定性测试
    from app.db import SessionLocal
    from app.models import AIModelEndpoint
    db = SessionLocal()
    endpoints = db.query(AIModelEndpoint).filter(
        AIModelEndpoint.enabled == True,  # noqa: E712
        AIModelEndpoint.adapter == "mock"
    ).all()
    db.close()
    assert len(endpoints) >= 1, "seed should provide at least one mock endpoint"
    eid = endpoints[0].id

    r = c.post("/ai-review/run", data={
        "project_id": "1", "scope": "equipment",
        "endpoint_id": str(eid), "user_instruction": "V0.2 回归",
    }, follow_redirects=False)
    assert r.status_code == 303
    jid = int(r.headers["location"].split("/")[-1])
    detail = c.get(f"/ai-review/{jid}")
    assert detail.status_code == 200
    api = c.get(f"/api/ai-review/{jid}").json()
    assert api["job"]["status"] == "completed"
    assert "result" in api


def test_health_check_standard_two_models(seeded_app, monkeypatch):
    from app.main import app

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    c = _get_client(app)
    from app.db import SessionLocal
    from app.models import AIModelEndpoint
    db = SessionLocal()
    endpoints = db.query(AIModelEndpoint).filter(
        AIModelEndpoint.enabled == True,  # noqa: E712
        AIModelEndpoint.adapter == "mock"
    ).all()
    db.close()
    assert len(endpoints) >= 2
    ids = [str(e.id) for e in endpoints[:2]]

    r = c.post("/health-check/run", data={
        "project_id": "1", "profile": "standard",
        "endpoint_ids": ids, "user_instruction": "V0.2 双模型回归",
    }, follow_redirects=False)
    assert r.status_code == 303
    bid = int(r.headers["location"].split("/")[-1])

    deadline = time.monotonic() + 10
    api = c.get(f"/api/health-check/{bid}").json()
    while api["batch"]["status"] in {"pending", "running"} and time.monotonic() < deadline:
        time.sleep(0.02)
        api = c.get(f"/api/health-check/{bid}").json()
    detail = c.get(f"/health-check/{bid}")
    assert detail.status_code == 200

    assert api["batch"]["status"] in ("completed", "completed_with_errors")
    assert "consensus" in api
    # 至少 1 项共识
    consensus = api["consensus"]
    assert consensus["overall_risk"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN")
    # score 是 0~100
    assert 0 <= consensus["score"] <= 100


def test_remediation_task_create_and_recheck(seeded_app, monkeypatch):
    from app.main import app

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    c = _get_client(app)
    r = c.post("/tasks/create", data={
        "project_id": "1", "scope": "equipment",
        "title": "V0.2 测试任务", "priority": "P1",
        "owner_role": "测试",
    }, follow_redirects=False)
    assert r.status_code == 303

    from app.db import SessionLocal
    from app.models import AIModelEndpoint, RemediationTask
    db = SessionLocal()
    task = db.query(RemediationTask).order_by(RemediationTask.id.desc()).first()
    assert task is not None
    assert task.status == "open"
    tid = task.id
    endpoint = db.query(AIModelEndpoint).filter(
        AIModelEndpoint.enabled == True,  # noqa: E712
        AIModelEndpoint.adapter == "mock"
    ).first()
    db.close()

    r = c.post(f"/tasks/{tid}/recheck", data={
        "endpoint_id": str(endpoint.id),
    }, follow_redirects=False)
    assert r.status_code == 303
    jid = int(r.headers["location"].split("/")[-1])

    db = SessionLocal()
    task = db.get(RemediationTask, tid)
    assert task.status == "rechecked"
    assert task.recheck_job_id == jid
    db.close()


def test_prompt_version_increments(seeded_app):
    from app.main import app

    c = _get_client(app)
    r = c.post("/ai-prompts", data={
        "name": "V0.2 测试模板",
        "scope": "equipment",
        "system_addendum": "v0.2 addendum",
        "review_focus": "v0.2 focus",
    }, follow_redirects=False)
    assert r.status_code == 303
    from app.db import SessionLocal
    from app.models import AIPromptTemplate
    db = SessionLocal()
    p = db.query(AIPromptTemplate).filter(
        AIPromptTemplate.scope == "equipment",
        AIPromptTemplate.version == 2,
    ).first()
    assert p is not None
    assert p.system_addendum == "v0.2 addendum"
    db.close()
