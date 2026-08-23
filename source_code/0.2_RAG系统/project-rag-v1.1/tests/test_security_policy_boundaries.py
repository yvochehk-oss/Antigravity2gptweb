"""专项安全策略边界测试（第二批整改）。

The tests in this module are intentionally independent from the shared
``test_security_controls`` smoke checks.  Configuration import is exercised
in a clean subprocess so a missing shared key cannot be hidden by an already
imported module or by the test runner's environment.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import security


RAG_ROOT = Path(__file__).resolve().parents[1]


def _config_subprocess(
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
    *,
    use_default_paths: bool = False,
):
    """Import ``app.config`` with only test-controlled security settings."""
    env = os.environ.copy()
    for key in (
        "PROJECT_RAG_DATA_DIR",
        "PROJECT_RAG_IMPORT_ROOT",
        "PROJECT_RAG_ALLOWED_IMPORT_ROOTS",
        "PROJECT_RAG_AUTH_REQUIRED",
        "PROJECT_RAG_HOST",
        "RAG_SHARED_API_KEY",
        "RAG_API_KEY",
        "PROJECT_RAG_API_KEY",
        "APP_ENV",
    ):
        env.pop(key, None)
    env.update({
        "PROJECT_RAG_HOST": "127.0.0.1",
        "PROJECT_RAG_AUTH_REQUIRED": "0",
    })
    if not use_default_paths:
        data_dir = tmp_path / "data"
        env.update({
            "PROJECT_RAG_DATA_DIR": str(data_dir),
            "PROJECT_RAG_IMPORT_ROOT": str(data_dir / "imports"),
        })
    if extra_env:
        env.update(extra_env)
    old_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(RAG_ROOT) + (os.pathsep + old_pythonpath if old_pythonpath else "")
    code = (
        "import json; from pathlib import Path; import app.config as c; "
        "print(json.dumps({'safe': [str(p) for p in c.SAFE_ORIGIN_DIRS], "
        "'allowed': [str(p) for p in c.ALLOWED_IMPORT_ROOTS]}))"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=RAG_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _last_json_line(stdout: str) -> dict:
    return json.loads(stdout.strip().splitlines()[-1])


def test_default_import_roots_do_not_grant_home_access(tmp_path):
    result = _config_subprocess(tmp_path, use_default_paths=True)
    assert result.returncode == 0, result.stderr
    body = _last_json_line(result.stdout)
    home = Path.home().resolve()

    assert body["allowed"] == []
    for raw in body["safe"]:
        root = Path(raw).resolve()
        assert root != home


def test_development_loopback_without_shared_key_keeps_optional_auth_contract(tmp_path):
    """Development on loopback remains usable without cross-system auth."""
    result = _config_subprocess(
        tmp_path,
        {"APP_ENV": "development", "PROJECT_RAG_HOST": "127.0.0.1"},
    )
    assert result.returncode == 0, result.stderr

    env = os.environ.copy()
    env.update(
        {
            "PROJECT_RAG_DATA_DIR": str(tmp_path / "data"),
            "PROJECT_RAG_IMPORT_ROOT": str(tmp_path / "data" / "imports"),
            "PROJECT_RAG_HOST": "127.0.0.1",
            "PROJECT_RAG_AUTH_REQUIRED": "0",
            "APP_ENV": "development",
            "PYTHONPATH": str(RAG_ROOT),
        }
    )
    env.pop("RAG_SHARED_API_KEY", None)
    env.pop("RAG_API_KEY", None)
    env.pop("PROJECT_RAG_API_KEY", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.config as c; assert c.AUTH_REQUIRED is False",
        ],
        cwd=RAG_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_explicit_import_root_is_allowed_and_resolved(tmp_path):
    explicit_root = (tmp_path / "operator-import").resolve()
    result = _config_subprocess(
        tmp_path,
        {"PROJECT_RAG_ALLOWED_IMPORT_ROOTS": str(explicit_root)},
    )
    assert result.returncode == 0, result.stderr
    body = _last_json_line(result.stdout)
    assert str(explicit_root) in body["allowed"]
    assert str(explicit_root) in body["safe"]

    # The configured root must be usable by the same path validator used by
    # folder imports, including for a not-yet-created destination file.
    env = os.environ.copy()
    env.update({
        "PROJECT_RAG_DATA_DIR": str(tmp_path / "data"),
        "PROJECT_RAG_IMPORT_ROOT": str(tmp_path / "data" / "imports"),
        "PROJECT_RAG_ALLOWED_IMPORT_ROOTS": str(explicit_root),
        "PROJECT_RAG_HOST": "127.0.0.1",
        "PROJECT_RAG_AUTH_REQUIRED": "0",
        "PYTHONPATH": str(RAG_ROOT),
        "TEST_IMPORT_PATH": str(explicit_root / "nested" / "document.md"),
    })
    validate_code = (
        "import os; from pathlib import Path; "
        "from app.services.storage.write import _validate_safe_path; "
        "print(_validate_safe_path(Path(os.environ['TEST_IMPORT_PATH'])))"
    )
    validated = subprocess.run(
        [sys.executable, "-c", validate_code],
        cwd=RAG_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert validated.returncode == 0, validated.stderr
    assert str(explicit_root / "nested" / "document.md") in validated.stdout


def test_explicit_import_root_rejects_parent_traversal(tmp_path, monkeypatch):
    from app.services.storage import write as storage_write

    allowed = (tmp_path / "allowed").resolve()
    outside = (tmp_path / "outside").resolve()
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setattr(storage_write, "SAFE_ORIGIN_DIRS", [allowed])

    with pytest.raises(storage_write.PathTraversalError):
        storage_write._validate_safe_path(allowed / ".." / "outside" / "secret.md")


def test_explicit_import_root_rejects_symlink_escape(tmp_path, monkeypatch):
    from app.services.storage import write as storage_write

    allowed = (tmp_path / "allowed").resolve()
    outside = (tmp_path / "outside").resolve()
    allowed.mkdir()
    outside.mkdir()
    (outside / "secret.md").write_text("not an import", encoding="utf-8")
    (allowed / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(storage_write, "SAFE_ORIGIN_DIRS", [allowed])

    with pytest.raises(storage_write.PathTraversalError):
        storage_write._validate_safe_path(allowed / "escape" / "secret.md")


@pytest.mark.parametrize(
    "mode_env",
    [
        {"PROJECT_RAG_AUTH_REQUIRED": "1"},
        {"APP_ENV": "production"},
    ],
    ids=["required-mode", "production-mode"],
)
def test_missing_shared_key_fails_closed_at_configuration_startup(tmp_path, mode_env):
    """Required and production services must not import without a shared key."""
    result = _config_subprocess(tmp_path, mode_env)
    assert result.returncode != 0
    assert "RAG_SHARED_API_KEY" in result.stderr


def test_legacy_tax_key_does_not_satisfy_rag_shared_key(tmp_path):
    """The legacy Tax-only key must not be derived or accepted by RAG."""
    result = _config_subprocess(
        tmp_path,
        {
            "APP_ENV": "production",
            "TAX_RAG_API_KEY": "legacy-tax-only-key",
        },
    )
    assert result.returncode != 0
    assert "RAG_SHARED_API_KEY" in result.stderr
    assert "legacy TAX_RAG_API_KEY cannot satisfy" in result.stderr


@pytest.mark.parametrize(
    "app_env",
    ["staging", "stage", "preprod", "pre-production"],
    ids=["staging", "stage", "preprod", "pre-production"],
)
@pytest.mark.parametrize(
    "shared_key",
    [None, "  \t\n"],
    ids=["missing", "blank"],
)
def test_preproduction_environments_fail_closed_without_nonblank_shared_key(
    tmp_path, app_env, shared_key
):
    """All documented pre-production names require an explicit shared key."""
    env = {"APP_ENV": app_env}
    if shared_key is not None:
        env["RAG_SHARED_API_KEY"] = shared_key

    result = _config_subprocess(tmp_path, env)

    assert result.returncode != 0
    assert "RAG_SHARED_API_KEY" in result.stderr


@pytest.mark.parametrize(
    "app_env",
    ["staging", "stage", "preprod", "pre-production"],
    ids=["staging", "stage", "preprod", "pre-production"],
)
def test_preproduction_environment_with_shared_key_starts_successfully(tmp_path, app_env):
    """A nonblank shared key permits each protected pre-production mode to start."""
    result = _config_subprocess(
        tmp_path,
        {
            "APP_ENV": app_env,
            "RAG_SHARED_API_KEY": "preproduction-test-key",
        },
    )

    assert result.returncode == 0, result.stderr


def test_production_loopback_with_shared_key_enables_required_auth(tmp_path):
    result = _config_subprocess(
        tmp_path,
        {
            "APP_ENV": "production",
            "PROJECT_RAG_HOST": "127.0.0.1",
            "RAG_SHARED_API_KEY": "production-test-key",
        },
    )
    assert result.returncode == 0, result.stderr

    env = os.environ.copy()
    env.update(
        {
            "PROJECT_RAG_DATA_DIR": str(tmp_path / "data"),
            "PROJECT_RAG_IMPORT_ROOT": str(tmp_path / "data" / "imports"),
            "PROJECT_RAG_HOST": "127.0.0.1",
            "PROJECT_RAG_AUTH_REQUIRED": "0",
            "APP_ENV": "production",
            "RAG_SHARED_API_KEY": "production-test-key",
            "PYTHONPATH": str(RAG_ROOT),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.config as c; assert c.AUTH_REQUIRED is True",
        ],
        cwd=RAG_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_bearer_auth_rejects_missing_or_wrong_key_and_uses_constant_time_compare(monkeypatch):
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "RAG_SHARED_API_KEY", "test-api-key")

    comparisons: list[tuple[str, str]] = []
    original_compare_digest = security.hmac.compare_digest

    def compare_digest(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return original_compare_digest(left, right)

    monkeypatch.setattr(security.hmac, "compare_digest", compare_digest)

    app = FastAPI()
    app.add_middleware(security.RAGSecurityMiddleware)

    @app.get("/api/v1/private")
    def private():
        return {"ok": True}

    client = TestClient(app)
    missing = client.get("/api/v1/private")
    wrong = client.get("/api/v1/private", headers={"Authorization": "Bearer wrong"})
    malformed = client.get("/api/v1/private", headers={"Authorization": "Basic test-api-key"})
    correct = client.get(
        "/api/v1/private",
        headers={"Authorization": "Bearer test-api-key"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert malformed.status_code == 401
    assert correct.status_code == 200
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert wrong.headers["WWW-Authenticate"] == "Bearer"
    assert any(left == "wrong" and right == "test-api-key" for left, right in comparisons)
    assert any(left == "test-api-key" and right == "test-api-key" for left, right in comparisons)
