"""用户认证：短期 HMAC Session Cookie + PBKDF2 密码哈希 + JWT 移动端令牌。

HMAC Session Cookie: 浏览器端使用，原有行为不变。
JWT Bearer Token: 移动端（Boss App / 第三方集成）使用。

The JWT secret is deliberately never read from a checked-in ``.env`` file.
Production deployments must inject it through the process environment;
development gets a per-process random secret so tokens cannot be forged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import time
from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import Request, Response

from .db import SessionLocal
from .models import User

# ---------------------------------------------------------------------------
# 密钥管理
# ---------------------------------------------------------------------------
_ENVIRONMENT = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
_SECRET = os.getenv("SESSION_SECRET_KEY", "").strip()
if not _SECRET:
    if _ENVIRONMENT in {"production", "prod", "staging"}:
        raise RuntimeError(
            "SESSION_SECRET_KEY must be provided through the process environment "
            "in production/staging; refusing the insecure demo fallback"
        )
    # A per-process random value is safe for local development and avoids a
    # globally shared demo secret.  Existing cookies are intentionally
    # invalidated on restart in this mode.
    _SECRET = secrets.token_urlsafe(48)

# ---------------------------------------------------------------------------
# Cookie 配置
# ---------------------------------------------------------------------------
COOKIE_NAME = "tax_session"
CSRF_COOKIE_NAME = "tax_csrf"
COOKIE_MAX_AGE = 60 * 60 * 8  # 8 小时
COOKIE_SECURE = os.getenv(
    "COOKIE_SECURE", "1" if _ENVIRONMENT in {"production", "prod", "staging"} else "0",
) not in {"0", "false", "False"}
MAX_CLOCK_SKEW = 60

# ---------------------------------------------------------------------------
# JWT 配置（移动端 Bearer Token）
# ---------------------------------------------------------------------------
_JWT_SECRET = os.getenv("JWT_SECRET_KEY", "").strip()
if not _JWT_SECRET:
    if _ENVIRONMENT in {"production", "prod", "staging"}:
        raise RuntimeError(
            "JWT_SECRET_KEY must be provided through the process environment "
            "in production/staging; refusing the insecure demo fallback"
        )
    _JWT_SECRET = secrets.token_urlsafe(48)

JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7
AUTH_SOURCE_CLAIM = "auth_source"
USER_CENTER_SOURCE = "user_center"
LEGACY_SOURCE = "legacy"
_AUTH_SOURCES = frozenset({USER_CENTER_SOURCE, LEGACY_SOURCE})


def _auth_source_for_user(user: Any) -> str:
    """Return the issuer namespace for a principal.

    User-center compatibility principals are explicitly marked at the
    boundary.  The legacy SQLAlchemy ``User`` model remains the fallback
    source, preserving existing Tax users without allowing an ID lookup to
    cross databases.
    """
    source = str(getattr(user, "auth_source", "") or "").strip().lower()
    return source if source in _AUTH_SOURCES else LEGACY_SOURCE


def _build_jwt_payload(user: User, role: str, *, token_type: str = "access") -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if token_type == "access":
        exp_delta = JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    else:
        exp_delta = JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    return {
        "sub": str(user.id),
        AUTH_SOURCE_CLAIM: _auth_source_for_user(user),
        "username": user.username,
        "role": user.role,
        "display_name": getattr(user, "display_name", None)
        or getattr(user, "nickname", None)
        or user.username,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + exp_delta,
    }


def issue_jwt(user: User) -> tuple[str, str]:
    """签发 access + refresh token 对。返回 (access_token, refresh_token)。

    Token payload 直接使用用户的正式角色（admin / operator），不再做翻译。
    """
    access_payload = _build_jwt_payload(user, user.role, token_type="access")
    refresh_payload = _build_jwt_payload(user, user.role, token_type="refresh")
    access_token = jwt.encode(access_payload, _JWT_SECRET, algorithm=JWT_ALGORITHM)
    refresh_token = jwt.encode(refresh_payload, _JWT_SECRET, algorithm=JWT_ALGORITHM)
    return access_token, refresh_token


def verify_jwt(token: str, *, expected_type: str = "access") -> dict[str, Any] | None:
    """校验 JWT，并拒绝没有来源命名空间的旧/不完整令牌。"""
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != expected_type:
            return None
        if payload.get(AUTH_SOURCE_CLAIM) not in _AUTH_SOURCES:
            # Numeric-only legacy subjects are ambiguous across the two user
            # stores; fail closed instead of guessing a database.
            return None
        return payload
    except jwt.PyJWTError:
        return None


def refresh_access_token(refresh_token: str) -> tuple[str, str] | None:
    """用 refresh token 换新 access + refresh token 对。失败返回 None。"""
    payload = verify_jwt(refresh_token, expected_type="refresh")
    if not payload:
        return None
    db = SessionLocal()
    try:
        source = payload.get(AUTH_SOURCE_CLAIM)
        if source == USER_CENTER_SOURCE:
            user = _get_user_by_id(int(payload["sub"]), source)
        elif source == LEGACY_SOURCE:
            user = db.query(User).filter(
                User.id == int(payload["sub"]),
                User.active == True,  # noqa: E712
            ).first()
        else:
            return None
        if not user:
            return None
        return issue_jwt(user)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 密码哈希（PBKDF2-SHA256）
# ---------------------------------------------------------------------------
def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 (hash_hex, salt_hex)。"""
    if salt is None:
        salt = secrets.token_bytes(32)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return dk.hex(), salt.hex()


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    """验证密码，返回 True/False。"""
    dk, _ = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(dk, hash_hex)


def validate_password_strength(password: str, username: str = "") -> None:
    """校验密码强度，不满足直接抛 ValueError。

    规则：至少 12 个字符，并同时包含至少 1 个大写字母、1 个小写字母、
    1 个数字和 1 个特殊字符；密码不得包含用户名。

    The function intentionally keeps its existing ``None`` return / ``ValueError``
    contract so callers such as ``seed`` can continue to validate a password
    before hashing it.  Upper/lower-case checks are ASCII-explicit.  For the
    special class, a non-alphanumeric, non-whitespace character is required;
    this keeps Unicode letters from being misclassified as punctuation while
    still allowing normal symbols and emoji.
    """
    if len(password) < 12:
        raise ValueError("密码长度至少为 12 个字符")
    if not re.search(r"[a-z]", password):
        raise ValueError("密码必须包含至少 1 个小写字母")
    if not re.search(r"[A-Z]", password):
        raise ValueError("密码必须包含至少 1 个大写字母")
    if not re.search(r"[0-9]", password):
        raise ValueError("密码必须包含至少 1 个数字")
    if not any(not char.isalnum() and not char.isspace() for char in password):
        raise ValueError("密码必须包含至少 1 个特殊字符")
    if username and username.lower() in password.lower():
        raise ValueError("密码不得包含用户名")


# ---------------------------------------------------------------------------
# Session 管理
# ---------------------------------------------------------------------------
def _session_token(user_id: int, auth_source: str = LEGACY_SOURCE) -> str:
    """生成带来源命名空间的 HMAC session token。"""
    if auth_source not in _AUTH_SOURCES:
        raise ValueError("invalid authentication source")
    payload = f"{auth_source}:{user_id}:{int(time.time())}".encode()
    sig = hmac.new(_SECRET.encode(), payload, "sha256").hexdigest()[:32]
    return payload.decode() + ":" + sig


def _parse_token(token: str) -> tuple[str, int] | None:
    """解析并校验 session token，返回 (auth_source, user_id) 或 None。

    The browser ``max_age`` is not an authorization boundary.  The server
    also validates the signed issue time so a copied cookie expires after the
    configured eight-hour lifetime.
    """
    try:
        payload, sig = token.rsplit(":", 1)
        expected = hmac.new(_SECRET.encode(), payload.encode(), "sha256").hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        parts = payload.split(":")
        if len(parts) != 3 or parts[0] not in _AUTH_SOURCES:
            return None
        auth_source = parts[0]
        user_id = int(parts[1])
        issued_at = int(parts[2])
        now = int(time.time())
        if issued_at > now + MAX_CLOCK_SKEW or now - issued_at > COOKIE_MAX_AGE:
            return None
        return auth_source, user_id
    except (ValueError, IndexError, TypeError):
        return None


def create_session(response: Response, user_id: int, auth_source: str = LEGACY_SOURCE) -> None:
    """在 Response 中写入带来源命名空间的签名 session cookie。"""
    token = _session_token(user_id, auth_source)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )
    # CSRF token is intentionally readable by same-origin browser JavaScript
    # and forms, while the session cookie remains HttpOnly.
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=secrets.token_urlsafe(32),
        max_age=COOKIE_MAX_AGE,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite="lax",
    )


def clear_session(response: Response) -> None:
    """清除 session cookie。"""
    response.delete_cookie(key=COOKIE_NAME, httponly=True, secure=COOKIE_SECURE, samesite="lax")
    response.delete_cookie(key=CSRF_COOKIE_NAME, httponly=False, secure=COOKIE_SECURE, samesite="lax")


# ---------------------------------------------------------------------------
# 请求上下文：从 cookie 读取当前用户
# ---------------------------------------------------------------------------
def _get_user_by_id(user_id: int, auth_source: str):
    """从令牌声明的来源数据库读取用户，绝不跨库回退。"""
    if auth_source == USER_CENTER_SOURCE:
        from .user_center.db import UserCenterSessionLocal
        from .user_center.models import UserAccount

        uc_db = UserCenterSessionLocal()
        try:
            u_acc = uc_db.get(UserAccount, user_id)
            if u_acc and getattr(u_acc, "active", True):
                class UserPrincipalCompat:
                    auth_source = USER_CENTER_SOURCE

                    def __init__(self, acc: UserAccount):
                        self.id = acc.id
                        self.username = acc.username
                        self.role = acc.role or "operator"
                        self.display_name = acc.nickname or acc.username
                        self.active = bool(getattr(acc, "active", True))
                        self.avatar_url = acc.avatar_url or "/static/avatars/default.png"
                return UserPrincipalCompat(u_acc)
        finally:
            uc_db.close()
        return None

    if auth_source == LEGACY_SOURCE:
        db = SessionLocal()
        try:
            return db.query(User).filter(User.id == user_id, User.active == True).first()
        finally:
            db.close()
    return None


def current_user_from_request(request: Request) -> Any | None:
    """从请求 header (Bearer JWT) 或 cookie 解析当前登录用户。"""
    # 1. 优先尝试从 Authorization Header 读取 Bearer JWT
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        token_str = auth_header[7:].strip()
        payload = verify_jwt(token_str, expected_type="access")
        if payload and payload.get("sub"):
            try:
                user_id = int(payload["sub"])
                return _get_user_by_id(user_id, payload[AUTH_SOURCE_CLAIM])
            except (ValueError, TypeError):
                pass

    # 2. 尝试从 cookie 读取 Session Token
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    parsed = _parse_token(token)
    if parsed is None:
        return None
    auth_source, user_id = parsed
    return _get_user_by_id(user_id, auth_source)


def login(username: str, password: str) -> Any | None:
    """验证用户名密码，优先从独立用户库 user_center.db 验证，回退到主库。"""
    from .user_center.db import UserCenterSessionLocal
    from .user_center.models import UserAccount
    from .user_center.security import verify_password as verify_user_center_pw

    # 1. 优先从独立用户数据库 user_center.db 验证
    uc_db = UserCenterSessionLocal()
    try:
        u_acc = uc_db.query(UserAccount).filter(
            UserAccount.username == username,
            UserAccount.active == True,
        ).first()
        if u_acc:
            if verify_user_center_pw(password, u_acc.password_hash):
                u_acc.updated_at = datetime.now(timezone.utc)
                uc_db.commit()
                class UserPrincipalCompat:
                    auth_source = USER_CENTER_SOURCE

                    def __init__(self, acc: UserAccount):
                        self.id = acc.id
                        self.username = acc.username
                        self.role = acc.role or "operator"
                        self.display_name = acc.nickname or acc.username
                        self.active = bool(getattr(acc, "active", True))
                        self.avatar_url = acc.avatar_url or "/static/avatars/default.png"
                return UserPrincipalCompat(u_acc)
    except Exception:
        pass
    finally:
        uc_db.close()

    # 2. 回退到 PostgreSQL users 表
    db = SessionLocal()
    try:
        user = db.query(User).filter(
            User.username == username,
            User.active == True,  # noqa: E712
        ).first()
        if user is None:
            return None
        parts = user.password_hash.split(":")
        if len(parts) == 2:
            hash_hex, salt_hex = parts
            if verify_password(password, hash_hex, salt_hex):
                user.last_login = datetime.now(timezone.utc).isoformat(timespec="seconds")
                db.commit()
                return user
        elif "$" in user.password_hash:
            if verify_user_center_pw(password, user.password_hash):
                return user
        return None
    finally:
        db.close()
