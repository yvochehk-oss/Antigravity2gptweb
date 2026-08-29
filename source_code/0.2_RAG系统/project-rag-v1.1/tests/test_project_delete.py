"""Test suite for project permanent deletion with password verification and cascade cleanup."""

from contextlib import contextmanager
import jwt
import pytest
from fastapi.testclient import TestClient

from app import security
from app.legacy_routes import app
from app.models import Project, Document

JWT_SECRET = "project-delete-secret-32-bytes"
ORIGIN = "http://127.0.0.1:8922"


def _token(role: str = "admin") -> str:
    return jwt.encode(
        {
            "sub": "1",
            "username": "admin",
            "role": role,
            "display_name": "系统管理员",
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture(autouse=True)
def _security_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "RAG_SHARED_API_KEY", "test_shared_key_123")


class _MockScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _MockDB:
    def __init__(self, project=None, docs=None):
        self.project = project
        self.docs = list(docs or [])
        self.deleted = []

    def get(self, model, obj_id):
        if model is Project and self.project and self.project.id == obj_id:
            return self.project
        return None

    def execute(self, statement):
        return _MockScalarResult(self.docs)

    def scalar(self, statement):
        return 0

    def delete(self, obj):
        self.deleted.append(obj)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_project_delete_wrong_password(monkeypatch):
    test_proj = Project(
        id=1,
        project_code="CD-TF-001",
        name="天府国际金融中心二期",
        contract_amount=1000000.0,
        location="成都市高新区",
        status="ACTIVE",
    )
    mock_db = _MockDB(project=test_proj, docs=[])

    @contextmanager
    def _mock_get_db():
        yield mock_db

    monkeypatch.setattr("app.legacy_routes.get_db", _mock_get_db)

    client = TestClient(app)
    cookies = {"cdjg_rag_token": _token()}
    headers = {"Origin": ORIGIN}

    # 尝试用错误密码删除项目
    resp = client.post(
        "/api/v1/projects/1/delete",
        json={"password": "wrong_password_xyz"},
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 403
    assert "密码错误" in resp.json().get("detail", "")


def test_project_delete_missing_password(monkeypatch):
    client = TestClient(app)
    cookies = {"cdjg_rag_token": _token()}
    headers = {"Origin": ORIGIN}

    resp = client.post(
        "/api/v1/projects/1/delete",
        json={"password": ""},
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 400
    assert "必须提供密码" in resp.json().get("detail", "")


def test_project_delete_not_found(monkeypatch):
    mock_db = _MockDB(project=None, docs=[])

    @contextmanager
    def _mock_get_db():
        yield mock_db

    monkeypatch.setattr("app.legacy_routes.get_db", _mock_get_db)

    client = TestClient(app)
    cookies = {"cdjg_rag_token": _token()}
    headers = {"Origin": ORIGIN}

    resp = client.post(
        "/api/v1/projects/9999/delete",
        json={"password": "admin123"},
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 404


def test_project_delete_success(monkeypatch):
    test_proj = Project(
        id=1,
        project_code="CD-TF-001",
        name="天府国际金融中心二期",
        contract_amount=1000000.0,
        location="成都市高新区",
        status="ACTIVE",
    )
    test_doc = Document(
        id=10,
        project_id=1,
        document_code="DOC-001",
        filename="合同.pdf",
        file_type="pdf",
        file_hash="hash01",
        original_path="",
        counterparty_code="EXT-PARTNER-01",
        parse_status="INDEXED",
    )
    mock_db = _MockDB(project=test_proj, docs=[test_doc])

    @contextmanager
    def _mock_get_db():
        yield mock_db

    monkeypatch.setattr("app.legacy_routes.get_db", _mock_get_db)

    client = TestClient(app)
    cookies = {"cdjg_rag_token": _token()}
    headers = {"Origin": ORIGIN}

    resp = client.post(
        "/api/v1/projects/1/delete",
        json={"password": "admin123"},
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("success") is True
    assert data.get("deleted_documents_count") == 1
    assert data.get("deleted_parties_count") == 1
    assert test_proj in mock_db.deleted

    # 也测试直接通过 DELETE 方法调用
    mock_db.deleted.clear()
    resp_del = client.request(
        "DELETE",
        "/api/v1/projects/1",
        json={"password": "admin123"},
        cookies=cookies,
        headers=headers,
    )
    assert resp_del.status_code == 200
    assert resp_del.json().get("success") is True
