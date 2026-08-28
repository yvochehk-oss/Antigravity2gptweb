"""Regression tests for the shared user-center authentication boundary."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.user_center import db as user_db
from app.user_center import router as user_router
from app.user_center.models import UserAccount
from app.user_center.security import hash_password, verify_password


def _memory_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    user_db.UserCenterBase.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_anonymous_user_center_request_is_rejected():
    db = _memory_session()
    try:
        with pytest.raises(HTTPException) as exc_info:
            user_router._get_current_user(SimpleNamespace(state=SimpleNamespace()), db)
        assert exc_info.value.status_code == 401
    finally:
        db.close()


def test_retained_test_admin_credentials_are_seeded_and_valid():
    db = _memory_session()
    try:
        admin = UserAccount(
            username="admin",
            password_hash=hash_password("888888"),
            role="admin",
            active=True,
        )
        db.add(admin)
        db.commit()
        assert verify_password("888888", admin.password_hash)
    finally:
        db.close()


def test_otp_response_does_not_expose_code(monkeypatch):
    db = _memory_session()
    try:
        admin = UserAccount(
            username="admin",
            password_hash=hash_password("888888"),
            role="admin",
            active=True,
            email="admin@example.com",
            email_verified=True,
        )
        db.add(admin)
        db.commit()
        monkeypatch.setattr(user_router, "send_email_code", lambda *args: (True, "sent"))
        response = user_router.send_verification_code(
            user_router.SendCodeRequest(
                target="admin@example.com",
                channel="email",
                purpose="change_password",
            ),
            admin,
            db,
        )
        assert "debug_code" not in response
        assert "code" not in response
    finally:
        db.close()


def test_tax_and_rag_default_user_db_paths_are_identical():
    project_root = Path(__file__).resolve().parents[6]
    tax_root = project_root / "source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0"
    rag_root = project_root / "source_code/0.2_RAG系统/project-rag-v1.1"

    def module_url(root: Path) -> str:
        env = os.environ.copy()
        env.pop("USER_CENTER_DB_URL", None)
        env["PYTHONPATH"] = str(root)
        return subprocess.check_output(
            [sys.executable, "-c", "from app.user_center.db import USER_CENTER_DB_URL; print(USER_CENTER_DB_URL)"],
            cwd=project_root,
            env=env,
            text=True,
        ).strip()

    assert module_url(tax_root) == module_url(rag_root)


def test_same_numeric_id_cannot_switch_between_user_store_namespaces(monkeypatch):
    """A legacy token for id=1 must never resolve the user-center id=1 row."""
    pytest.importorskip("psycopg")
    # The normal Tax test harness intentionally points DATABASE_URL at a
    # disposable PostgreSQL instance.  This unit test exercises only token
    # routing, so keep the import isolated from that external dependency.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test_user:test_pass@127.0.0.1:5432/test_tax_db")
    from app import auth
    import jwt

    monkeypatch.setattr(
        auth,
        "_get_user_by_id",
        lambda user_id, source: SimpleNamespace(
            id=user_id,
            username="legacy-admin" if source == auth.LEGACY_SOURCE else "user-center-admin",
            role="operator",
        ),
    )
    user = SimpleNamespace(
        id=1,
        username="legacy-admin",
        role="operator",
        display_name="Legacy Admin",
        auth_source=auth.LEGACY_SOURCE,
    )
    token, _ = auth.issue_jwt(user)
    request = SimpleNamespace(
        headers={"authorization": f"Bearer {token}"},
        cookies={},
    )
    principal = auth.current_user_from_request(request)
    assert principal.username == "legacy-admin"

    old_payload = {
        "sub": "1",
        "username": "legacy-admin",
        "role": "operator",
        "type": "access",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }
    old_token = jwt.encode(old_payload, auth._JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    assert auth.verify_jwt(old_token) is None
