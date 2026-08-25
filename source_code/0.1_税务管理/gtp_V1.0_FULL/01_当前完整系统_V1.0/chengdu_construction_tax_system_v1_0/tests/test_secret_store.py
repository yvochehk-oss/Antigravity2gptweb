from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def _store(tmp_path: Path):
    from app.services.secret_store import SecretStore

    return SecretStore(
        credentials_file=tmp_path / "config" / "ai-credentials.enc",
        root_key_file=tmp_path / "config" / "ai-secret-root.key",
    )


def test_secret_store_encrypts_at_rest_and_applies_permissions(tmp_path):
    store = _store(tmp_path)
    secret = "sk-live-仅存在密文-9f7b"
    ref = store.put(secret)

    assert len(ref) == 36
    assert store.get(ref) == secret
    ciphertext = store.credentials_file.read_bytes()
    assert secret.encode() not in ciphertext
    assert store.credentials_file.stat().st_mode & 0o777 == 0o600
    assert store.root_key_file.stat().st_mode & 0o777 == 0o600
    assert store.credentials_file.parent.stat().st_mode & 0o777 == 0o700

    metadata = store.metadata(ref)
    assert metadata == {
        "ref": ref,
        "configured": True,
        "revoked": False,
        "created_at": metadata["created_at"],
        "updated_at": metadata["updated_at"],
    }


def test_secret_store_revoke_and_delete_are_not_reversible_reads(tmp_path):
    store = _store(tmp_path)
    ref = store.put("secret-value")
    assert store.revoke(ref) is True
    assert store.get(ref) is None
    assert store.metadata(ref)["revoked"] is True
    assert store.delete(ref) is True
    assert store.metadata(ref) is None
    assert store.delete(ref) is False


def test_secret_store_concurrent_writes_preserve_all_records(tmp_path):
    store = _store(tmp_path)

    def put(i: int) -> str:
        return store.put(f"key-{i:03d}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        refs = list(executor.map(put, range(24)))
    assert len(set(refs)) == 24
    assert sorted(store.get(ref) for ref in refs) == [f"key-{i:03d}" for i in range(24)]
    assert len(store.list_metadata()) == 24


def test_secret_store_rejects_control_characters_and_invalid_refs(tmp_path):
    store = _store(tmp_path)
    from app.services.secret_store import SecretStoreError

    with pytest.raises(SecretStoreError, match="控制字符"):
        store.put("secret\nwith-newline")
    with pytest.raises(SecretStoreError, match="为空"):
        store.put("   ")
    with pytest.raises(SecretStoreError, match="引用无效"):
        store.get("not-a-ref")


def test_secret_store_tamper_fails_closed_without_secret_in_error(tmp_path):
    store = _store(tmp_path)
    store.put("do-not-leak-this")
    raw = store.credentials_file.read_text(encoding="utf-8")
    store.credentials_file.write_text(raw.replace("ciphertext", "ciphertextx"), encoding="utf-8")
    store.credentials_file.chmod(0o600)
    from app.services.secret_store import SecretStoreError

    with pytest.raises(SecretStoreError) as error:
        store.list_metadata()
    assert "do-not-leak-this" not in str(error.value)


def test_model_admin_validator_rejects_mock_and_allows_local_llama(monkeypatch):
    from app.routers.models import _validate_form

    monkeypatch.setenv("AI_ALLOW_PRIVATE_LLM", "1")
    with pytest.raises(ValueError, match="mock"):
        _validate_form(
            name="legacy", adapter="mock", base_url="", chat_path="", model="mock",
            api_key_env="", timeout_seconds=10, note="", priority=100,
            routing_group="default", api_key="",
        )
    values = _validate_form(
        name="local-3b", adapter="openai_compatible", base_url="http://127.0.0.1:8080",
        chat_path="/v1/chat/completions", model="qwen3-3b", api_key_env="",
        timeout_seconds=20, note="llama.cpp", priority=900, routing_group="local",
        api_key="",
    )
    assert values["api_key"] == ""
    assert values["adapter"] == "openai_compatible"
