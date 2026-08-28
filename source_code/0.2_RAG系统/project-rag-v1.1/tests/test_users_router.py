"""Tests for RAG user management endpoints."""
import time
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import _get_jwt_secret
from app.main import app
from app.user_center.db import get_user_center_db
from app.user_center.models import UserAccount

ORIGIN_HEADER = {"Origin": "http://127.0.0.1:8922"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_cookie():
    secret = _get_jwt_secret() or "bf854ff273aa1b54b2358286b4113e5a8f9cffa772da8bc89f05ccab502d8ef3"
    token = jwt.encode(
        {"sub": "1", "username": "admin", "role": "admin", "display_name": "系统管理员", "type": "access"},
        secret,
        algorithm="HS256",
    )
    return {"cdjg_rag_token": token}


def test_users_page_renders_with_admin(client, admin_cookie):
    client.cookies.update(admin_cookie)
    res = client.get("/users")
    assert res.status_code == 200
    assert "统一用户权限管理中心" in res.text
    assert "btn-edit-user" in res.text
    assert "btn-reset-password" in res.text


def test_users_api_list(client, admin_cookie):
    client.cookies.update(admin_cookie)
    res = client.get("/api/v1/users")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert any(u["username"] == "admin" for u in data)


def test_create_update_reset_toggle_user_lifecycle(client, admin_cookie):
    client.cookies.update(admin_cookie)
    ts = int(time.time() * 1000)
    unique_user = f"testuser_{ts}"
    unique_email = f"test_{ts}@cd-construction.com"
    unique_phone = f"139{str(ts)[-8:]}"
    updated_email = f"updated_{ts}@cd-construction.com"
    updated_phone = f"138{str(ts)[-8:]}"

    user_id = None
    try:
        # 1. Create user
        res = client.post(
            "/api/v1/users",
            headers=ORIGIN_HEADER,
            json={
                "username": unique_user,
                "nickname": "测试用户",
                "password": "Password123!",
                "role": "operator",
                "email": unique_email,
                "phone": unique_phone,
            },
        )
        assert res.status_code == 201
        created = res.json()
        user_id = created["id"]
        assert created["username"] == unique_user
        assert created["nickname"] == "测试用户"
        assert created["active"] is True

        # 2. Update user
        res = client.post(
            f"/api/v1/users/{user_id}/update",
            headers=ORIGIN_HEADER,
            json={
                "nickname": "修改后的昵称",
                "role": "operator",
                "phone": updated_phone,
                "email": updated_email,
            },
        )
        assert res.status_code == 200
        updated = res.json()
        assert updated["nickname"] == "修改后的昵称"
        assert updated["email"] == updated_email
        assert updated["phone"] == updated_phone

        # 3. Reset password
        res = client.post(
            f"/api/v1/users/{user_id}/reset-password",
            headers=ORIGIN_HEADER,
            json={"new_password": "NewSecretPassword456!"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        # 4. Toggle active
        res = client.post(f"/api/v1/users/{user_id}/toggle-active", headers=ORIGIN_HEADER)
        assert res.status_code == 200
        assert res.json()["active"] is False

        # Toggle active again
        res = client.post(f"/api/v1/users/{user_id}/toggle-active", headers=ORIGIN_HEADER)
        assert res.status_code == 200
        assert res.json()["active"] is True
    finally:
        if user_id:
            for session in get_user_center_db():
                u = session.get(UserAccount, user_id)
                if u:
                    session.delete(u)
                    session.commit()
                break
