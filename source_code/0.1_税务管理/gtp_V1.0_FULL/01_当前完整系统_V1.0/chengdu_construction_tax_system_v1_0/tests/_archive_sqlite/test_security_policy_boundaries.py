"""专项安全策略边界测试（第二批整改）。

These tests deliberately exercise the policy boundaries called out in the
security remediation plan.  They do not weaken the assertions to match a
legacy implementation: the password policy is twelve characters with upper
and lower case, a digit, and a special character, while seed must fail closed
without an initialization password and preserve an existing user's hash.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TAX_ROOT = Path(__file__).resolve().parents[1]


def _run_config_import(**updates: str | None) -> subprocess.CompletedProcess[str]:
    """Import ``app.config`` in a clean process with controlled environment.

    Configuration is evaluated at module import time.  A subprocess gives the
    startup assertions a real import boundary without poisoning the app module
    cache used by the rest of this test suite.
    """
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(TAX_ROOT)})
    for key, value in updates.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.config as c; print(repr(c.RAG_SHARED_API_KEY))",
        ],
        cwd=TAX_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("missing_value", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_tax_rag_shared_key_fails_closed_when_auth_is_required(missing_value):
    """A legacy client key must never satisfy Tax→RAG authentication."""
    result = _run_config_import(
        APP_ENV="production",
        TAX_AUTO_SYNC_ENABLED="0",
        TAX_RAG_AUTH_REQUIRED="0",
        TAX_RAG_API_KEY="legacy-client-only-key",
        RAG_SHARED_API_KEY=missing_value,
    )

    assert result.returncode != 0
    assert "RAG_SHARED_API_KEY" in result.stderr
    assert "TAX_RAG_API_KEY fallback" in result.stderr


def test_tax_rag_shared_key_is_required_for_explicit_auth_in_development():
    """A non-production process can still opt into the strict auth boundary."""
    result = _run_config_import(
        APP_ENV="development",
        TAX_AUTO_SYNC_ENABLED="0",
        TAX_RAG_AUTH_REQUIRED="1",
        TAX_RAG_API_KEY="legacy-client-only-key",
        RAG_SHARED_API_KEY="",
    )

    assert result.returncode != 0
    assert "RAG_SHARED_API_KEY" in result.stderr


def test_tax_rag_shared_key_explicit_configuration_succeeds_and_is_trimmed():
    result = _run_config_import(
        APP_ENV="production",
        TAX_RAG_API_KEY="legacy-client-only-key",
        RAG_SHARED_API_KEY="  current-shared-key  ",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "'current-shared-key'"


def test_local_mode_without_rag_credentials_remains_importable():
    """Local mode without any RAG credentials remains importable."""
    result = _run_config_import(
        APP_ENV="development",
        TAX_AUTO_SYNC_ENABLED="0",
        TAX_RAG_AUTH_REQUIRED="0",
        TAX_RAG_API_KEY=None,
        RAG_SHARED_API_KEY=None,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "''"


def test_legacy_tax_rag_key_alone_cannot_enable_a_shared_key_fallback():
    """A legacy client key must fail closed even outside production."""
    result = _run_config_import(
        APP_ENV="development",
        TAX_AUTO_SYNC_ENABLED="0",
        TAX_RAG_AUTH_REQUIRED="0",
        TAX_RAG_API_KEY="legacy-client-only-key",
        RAG_SHARED_API_KEY=None,
    )

    assert result.returncode != 0
    assert "RAG_SHARED_API_KEY" in result.stderr


@pytest.mark.parametrize(
    "password",
    [
        "Aa1!bbbbbbb",  # eleven characters: below the twelve-character minimum
        "AA1!BBBBBBBB",  # no lowercase
        "aa1!bbbbbbbb",  # no uppercase
        "Aa!bbbbbbbbb",  # no digit
        "Aa1bbbbbbbbb",  # no special character
    ],
    ids=["too-short", "no-lowercase", "no-uppercase", "no-digit", "no-special"],
)
def test_password_strength_rejects_each_missing_requirement(password):
    """Every password policy dimension must fail closed at its boundary."""
    from app.auth import validate_password_strength

    with pytest.raises(ValueError):
        validate_password_strength(password, username="operator")


def test_password_strength_accepts_exact_twelve_character_complete_password():
    """The exact minimum length is accepted when all character classes exist."""
    from app.auth import validate_password_strength

    # 12 characters: A a 1 ! + eight lower-case letters.
    validate_password_strength("Aa1!bbbbbbbb", username="operator")


def test_password_strength_rejects_username_even_when_other_requirements_exist():
    from app.auth import validate_password_strength

    with pytest.raises(ValueError):
        validate_password_strength("Operator1!Xx", username="operator")


def _isolated_seed(monkeypatch, tmp_path: Path):
    """Bind ``app.seed`` to a private SQLite database for one test."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import seed as seed_module
    from app.db import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'seed-policy.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(seed_module, "engine", engine)
    monkeypatch.setattr(seed_module, "SessionLocal", sessions)
    Base.metadata.create_all(engine)
    return seed_module, engine, sessions


def test_seed_fails_closed_without_initial_admin_password(monkeypatch, tmp_path):
    """A fresh database must not be bootstrapped with a known password."""
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("INITIAL_OPERATOR_PASSWORD", raising=False)
    seed_module, engine, sessions = _isolated_seed(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="INITIAL_ADMIN_PASSWORD"):
        seed_module.run()

    from app.models import User

    with sessions() as db:
        assert db.query(User).count() == 0
    engine.dispose()


def test_seed_reports_current_password_policy(monkeypatch, tmp_path):
    """Seed's wrapper error must describe the same policy as auth."""
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Aa1!bbbbbbb")
    seed_module, engine, _sessions = _isolated_seed(monkeypatch, tmp_path)

    with pytest.raises(
        RuntimeError,
        match=r"at least 12 characters.*upper/lowercase.*digit.*special character",
    ) as exc_info:
        seed_module.run()

    assert "≥10" not in str(exc_info.value)
    engine.dispose()


def test_seed_preserves_existing_user_password_hash(monkeypatch, tmp_path):
    """Re-seeding must never replace an existing administrator password."""
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "NewSeedPass1!")
    monkeypatch.setenv("INITIAL_OPERATOR_PASSWORD", "NewOperator1!")
    seed_module, engine, sessions = _isolated_seed(monkeypatch, tmp_path)

    from app.auth import hash_password
    from app.models import User

    old_hash, old_salt = hash_password("ExistingAdmin1!")
    existing_password_hash = f"{old_hash}:{old_salt}"
    with sessions() as db:
        db.add(User(
            username="admin",
            password_hash=existing_password_hash,
            role="manager",
            display_name="已有管理员",
            active=True,
            created_at="2026-08-20T00:00:00+00:00",
        ))
        db.commit()

    seed_module.run()

    with sessions() as db:
        admin = db.query(User).filter(User.username == "admin").one()
        assert admin.password_hash == existing_password_hash
    engine.dispose()
