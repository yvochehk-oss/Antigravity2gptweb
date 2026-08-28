"""Encrypted storage for user-supplied AI credentials.

Only an opaque UUID reference is intended to cross the application/database
boundary.  Credential values are encrypted at rest with AES-256-GCM using a
separate, locally protected 32-byte root key.  This module deliberately does
not use the database, application logs, audit rows, or an environment
variable as a credential vault.

The default files live outside the checkout::

    ~/.config/chengdu-construction/ai-credentials.enc
    ~/.config/chengdu-construction/ai-secret-root.key

``AI_CREDENTIALS_FILE`` and ``AI_SECRET_ROOT_FILE`` may override those paths
for an explicitly managed deployment or a disposable test HOME.  Overrides
are still written atomically with restrictive permissions.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:  # pragma: no cover - exercised on the supported Unix deployments
    import fcntl
except ImportError:  # pragma: no cover - Tax is deployed on macOS/Linux
    fcntl = None  # type: ignore[assignment]


class SecretStoreError(RuntimeError):
    """A safe, non-secret storage/configuration error."""


_VERSION = 1
_ALGORITHM = "AES-256-GCM"
_AAD = b"chengdu-construction-ai-credentials-v1"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_SECRET_BYTES = 64 * 1024
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_REF_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
)


def _config_dir() -> Path:
    return Path(
        os.getenv(
            "AI_SECRET_CONFIG_DIR",
            "~/.config/chengdu-construction",
        ),
    ).expanduser()


def _configured_path(env_name: str, default_name: str) -> Path:
    raw = os.getenv(env_name, "").strip()
    return Path(raw).expanduser() if raw else _config_dir() / default_name


def _mode(path: Path) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:  # pragma: no cover - caller handles with context
        raise SecretStoreError("秘密存储文件不可访问") from exc


def _ensure_private_directory(path: Path) -> None:
    """Create the parent and enforce 0700 without following a symlink."""
    path = path.expanduser()
    try:
        if path.exists() and path.is_symlink():
            raise SecretStoreError("秘密存储目录不能是符号链接")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise SecretStoreError("秘密存储目录无效")
        os.chmod(path, 0o700)
    except SecretStoreError:
        raise
    except OSError as exc:
        raise SecretStoreError("无法准备秘密存储目录") from exc


def _reject_symlink(path: Path, message: str) -> None:
    try:
        if path.is_symlink():
            raise SecretStoreError(message)
    except OSError as exc:
        raise SecretStoreError(message) from exc


def _validate_secret(value: str) -> str:
    if not isinstance(value, str):
        raise SecretStoreError("API Key 必须是文本")
    if not value or not value.strip() or len(value.encode("utf-8")) > _MAX_SECRET_BYTES:
        raise SecretStoreError("API Key 为空或超出允许大小")
    if _CONTROL_RE.search(value):
        raise SecretStoreError("API Key 不得包含控制字符")
    return value


def _validate_ref(ref: str) -> str:
    candidate = str(ref or "").strip()
    if not _REF_RE.fullmatch(candidate):
        raise SecretStoreError("凭证引用无效")
    return candidate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SecretStore:
    """Small encrypted credential vault with process-safe file locking.

    Every mutating operation takes an exclusive lock, decrypts the current
    envelope, changes the in-memory record, and atomically replaces the
    ciphertext file.  Callers only receive the secret from ``get`` when they
    explicitly need to make an outbound request.
    """

    def __init__(
        self,
        credentials_file: str | os.PathLike[str] | None = None,
        root_key_file: str | os.PathLike[str] | None = None,
    ) -> None:
        self.credentials_file = Path(
            credentials_file
            if credentials_file is not None
            else _configured_path("AI_CREDENTIALS_FILE", "ai-credentials.enc"),
        ).expanduser()
        self.root_key_file = Path(
            root_key_file
            if root_key_file is not None
            else _configured_path("AI_SECRET_ROOT_FILE", "ai-secret-root.key"),
        ).expanduser()
        if self.credentials_file == self.root_key_file:
            raise SecretStoreError("凭证文件与根密钥文件不能相同")
        self._lock_file = self.credentials_file.with_name(
            f".{self.credentials_file.name}.lock",
        )

    @contextmanager
    def _lock(self) -> Iterator[None]:
        _ensure_private_directory(self.credentials_file.parent)
        _ensure_private_directory(self.root_key_file.parent)
        _reject_symlink(self._lock_file, "秘密存储锁文件不能是符号链接")
        try:
            lock_handle = open(self._lock_file, "a+b")  # noqa: SIM115
            os.chmod(self._lock_file, 0o600)
        except OSError as exc:
            raise SecretStoreError("无法打开秘密存储锁") from exc
        try:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()

    def _read_root_key_unlocked(self) -> bytes:
        _reject_symlink(self.root_key_file, "秘密存储根密钥不能是符号链接")
        try:
            if self.root_key_file.exists():
                if not self.root_key_file.is_file():
                    raise SecretStoreError("秘密存储根密钥文件无效")
                raw = self.root_key_file.read_bytes()
                if len(raw) != _KEY_BYTES:
                    raise SecretStoreError("秘密存储根密钥无效")
                os.chmod(self.root_key_file, 0o600)
                return raw

            self.root_key_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(self.root_key_file, flags, 0o600)
            try:
                raw = secrets.token_bytes(_KEY_BYTES)
                with os.fdopen(fd, "wb", closefd=True) as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(self.root_key_file, 0o600)
                return raw
            except Exception:
                # Do not leave a partially-created root key behind.
                with suppress(OSError):
                    self.root_key_file.unlink(missing_ok=True)
                raise
        except SecretStoreError:
            raise
        except FileExistsError:
            # A second process may have created the key between exists() and
            # O_EXCL.  Validate and use that complete key.
            return self._read_root_key_unlocked()
        except OSError as exc:
            raise SecretStoreError("秘密存储根密钥不可访问") from exc

    @staticmethod
    def _decode_b64(value: Any, *, field: str) -> bytes:
        if not isinstance(value, str):
            raise SecretStoreError(f"秘密存储格式无效: {field}")
        try:
            decoded = base64.urlsafe_b64decode(value.encode("ascii"))
        except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
            raise SecretStoreError(f"秘密存储格式无效: {field}") from exc
        return decoded

    def _load_unlocked(self, key: bytes) -> dict[str, Any]:
        _reject_symlink(self.credentials_file, "秘密存储文件不能是符号链接")
        try:
            if not self.credentials_file.exists():
                return {"version": _VERSION, "credentials": {}}
            if not self.credentials_file.is_file() or (os.name != "nt" and _mode(self.credentials_file) & 0o077):
                raise SecretStoreError("秘密存储文件权限不安全")
            if self.credentials_file.stat().st_size > _MAX_FILE_BYTES:
                raise SecretStoreError("秘密存储文件超出允许大小")
            envelope = json.loads(self.credentials_file.read_text(encoding="utf-8"))
        except SecretStoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SecretStoreError("秘密存储文件不可读") from exc

        if not isinstance(envelope, dict):
            raise SecretStoreError("秘密存储格式无效")
        if envelope.get("version") != _VERSION or envelope.get("alg") != _ALGORITHM:
            raise SecretStoreError("秘密存储版本或算法不受支持")
        nonce = self._decode_b64(envelope.get("nonce"), field="nonce")
        ciphertext = self._decode_b64(envelope.get("ciphertext"), field="ciphertext")
        if len(nonce) != _NONCE_BYTES or len(ciphertext) < 16:
            raise SecretStoreError("秘密存储密文无效")
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, _AAD)
            data = json.loads(plaintext.decode("utf-8"))
        except (InvalidTag, UnicodeError, json.JSONDecodeError) as exc:
            raise SecretStoreError("秘密存储校验失败") from exc
        if not isinstance(data, dict) or data.get("version") != _VERSION:
            raise SecretStoreError("秘密存储内容无效")
        records = data.get("credentials")
        if not isinstance(records, dict):
            raise SecretStoreError("秘密存储内容无效")
        # Validate all records before exposing any value to a caller.  This
        # also keeps a tampered-but-authenticated format from becoming an
        # arbitrary object source for routes.
        for ref, record in records.items():
            if not isinstance(ref, str) or not _REF_RE.fullmatch(ref):
                raise SecretStoreError("秘密存储内容无效")
            if not isinstance(record, dict):
                raise SecretStoreError("秘密存储内容无效")
            value = record.get("secret")
            if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_SECRET_BYTES:
                raise SecretStoreError("秘密存储内容无效")
            if _CONTROL_RE.search(value):
                raise SecretStoreError("秘密存储内容无效")
            if not isinstance(record.get("revoked", False), bool):
                raise SecretStoreError("秘密存储内容无效")
        return {"version": _VERSION, "credentials": records}

    def _write_unlocked(self, key: bytes, data: dict[str, Any]) -> None:
        payload = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(nonce, payload, _AAD)
        envelope = {
            "version": _VERSION,
            "alg": _ALGORITHM,
            "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        }
        encoded = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(encoded) > _MAX_FILE_BYTES:
            raise SecretStoreError("秘密存储内容超出允许大小")
        _reject_symlink(self.credentials_file, "秘密存储文件不能是符号链接")
        self.credentials_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = -1
        temp_name = ""
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self.credentials_file.name}.",
                suffix=".tmp",
                dir=self.credentials_file.parent,
            )
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                fd = -1
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.credentials_file)
            temp_name = ""
            os.chmod(self.credentials_file, 0o600)
            try:
                dir_fd = os.open(self.credentials_file.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                # The file replacement is still atomic on filesystems where
                # directory fsync is unavailable; do not expose a secret.
                pass
        except OSError as exc:
            raise SecretStoreError("秘密存储文件写入失败") from exc
        finally:
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
            if temp_name:
                with suppress(OSError):
                    os.unlink(temp_name)

    def put(self, secret: str) -> str:
        """Store a credential and return a new opaque UUID reference."""
        value = _validate_secret(secret)
        with self._lock():
            key = self._read_root_key_unlocked()
            data = self._load_unlocked(key)
            ref = str(uuid.uuid4())
            data["credentials"][ref] = {
                "secret": value,
                "created_at": _now(),
                "updated_at": _now(),
                "revoked": False,
            }
            self._write_unlocked(key, data)
            return ref

    # Explicit alias used by route/service callers; keeping ``put`` short
    # makes accidental inclusion of a secret in a variable name less likely.
    store = put
    put_secret = put

    def get(self, ref: str) -> str | None:
        """Return an active credential, or ``None`` for missing/revoked refs."""
        candidate = _validate_ref(ref)
        with self._lock():
            key = self._read_root_key_unlocked()
            record = self._load_unlocked(key)["credentials"].get(candidate)
            if not isinstance(record, dict) or record.get("revoked", False):
                return None
            value = record.get("secret")
            return value if isinstance(value, str) else None

    # Adapter-friendly aliases.
    resolve = get
    get_secret = get
    resolve_credential = get

    def metadata(self, ref: str) -> dict[str, Any] | None:
        """Return non-secret metadata only."""
        candidate = _validate_ref(ref)
        with self._lock():
            key = self._read_root_key_unlocked()
            record = self._load_unlocked(key)["credentials"].get(candidate)
            if not isinstance(record, dict):
                return None
            return {
                "ref": candidate,
                "configured": not bool(record.get("revoked", False)),
                "revoked": bool(record.get("revoked", False)),
                "created_at": str(record.get("created_at") or ""),
                "updated_at": str(record.get("updated_at") or ""),
            }

    def list_metadata(self) -> list[dict[str, Any]]:
        with self._lock():
            key = self._read_root_key_unlocked()
            records = self._load_unlocked(key)["credentials"]
            return [
                {
                    "ref": ref,
                    "configured": not bool(record.get("revoked", False)),
                    "revoked": bool(record.get("revoked", False)),
                    "created_at": str(record.get("created_at") or ""),
                    "updated_at": str(record.get("updated_at") or ""),
                }
                for ref, record in records.items()
            ]

    def revoke(self, ref: str) -> bool:
        """Revoke a reference while retaining a non-reusable audit-safe record."""
        candidate = _validate_ref(ref)
        with self._lock():
            key = self._read_root_key_unlocked()
            data = self._load_unlocked(key)
            record = data["credentials"].get(candidate)
            if not isinstance(record, dict):
                return False
            if not record.get("revoked", False):
                record["revoked"] = True
                record["updated_at"] = _now()
                self._write_unlocked(key, data)
            return True

    def delete(self, ref: str) -> bool:
        """Permanently remove a credential record from the encrypted vault."""
        candidate = _validate_ref(ref)
        with self._lock():
            key = self._read_root_key_unlocked()
            data = self._load_unlocked(key)
            if candidate not in data["credentials"]:
                return False
            del data["credentials"][candidate]
            self._write_unlocked(key, data)
            return True

    def rotate(self, old_ref: str | None, secret: str) -> str:
        """Create a new ref; caller switches its DB reference before deletion."""
        new_ref = self.put(secret)
        # Do not revoke/delete here: the caller must first commit the new DB
        # reference, then explicitly retire the old one.
        _ = old_ref
        return new_ref


def default_secret_store() -> SecretStore:
    """Construct the current process-configured store (no stale singleton)."""
    return SecretStore()


def store_secret(secret: str) -> str:
    return default_secret_store().put(secret)


def resolve_secret(ref: str) -> str | None:
    return default_secret_store().get(ref)


def get_secret(ref: str) -> str | None:
    return resolve_secret(ref)


def resolve_credential(ref: str) -> str | None:
    return resolve_secret(ref)


def delete_secret(ref: str) -> bool:
    return default_secret_store().delete(ref)


def revoke_secret(ref: str) -> bool:
    return default_secret_store().revoke(ref)


__all__ = [
    "SecretStore",
    "SecretStoreError",
    "default_secret_store",
    "delete_secret",
    "get_secret",
    "revoke_secret",
    "resolve_credential",
    "resolve_secret",
    "store_secret",
]
