"""Security helpers for outbound integrations and input boundaries.

This module is intentionally small and dependency-free.  It does not fetch
URLs or read secrets; it only validates configuration before an adapter is
allowed to make an outbound request.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit


_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "instance-data",
}


def allowed_secret_env_names() -> frozenset[str]:
    """Return the deployment-approved secret identifiers.

    The allow-list is configured as names only (never values), so a database
    record cannot name arbitrary environment variables such as DATABASE_URL.
    """
    raw = os.getenv(
        "AI_ALLOWED_SECRET_ENV_NAMES",
        "OPENAI_API_KEY,OPENROUTER_API_KEY,RAG_LLM_API_KEY,TENCENT_HUNYUAN_API_KEY,TAX_AI_API_KEY",
    )
    names = {x.strip() for x in raw.split(",") if x.strip()}
    return frozenset(x for x in names if _SECRET_NAME_RE.fullmatch(x))


def resolve_secret_env_name(value: str | None) -> str | None:
    """Validate a configured secret name; reject values and arbitrary names."""
    name = str(value or "").strip()
    if not name:
        return None
    if not _SECRET_NAME_RE.fullmatch(name) or name not in allowed_secret_env_names():
        raise ValueError("api_key_env must be an approved secret identifier")
    return name


def validate_ai_endpoint_url(base_url: str, chat_path: str = "") -> str:
    """Validate an AI endpoint and reject obvious SSRF targets.

    Public integrations must use HTTPS and an optional host allow-list.  A
    local HTTP endpoint is allowed only when explicitly enabled for local
    development, and still cannot be used in production.
    """
    raw = str(base_url or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("AI base_url must be an absolute http(s) URL")
    host = parsed.hostname.rstrip(".").lower()
    if host in _BLOCKED_HOSTS:
        raise ValueError("AI endpoint host is not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_multicast or address.is_reserved or address.is_unspecified
    ):
        if not (
            os.getenv("AI_ALLOW_INSECURE_LOCAL", "0") == "1"
            and os.getenv("APP_ENV", "development").lower() not in {"prod", "production", "staging"}
            and address.is_loopback
        ):
            raise ValueError("AI endpoint must not target a private or loopback IP")

    configured_hosts = {
        x.strip().lower().rstrip(".")
        for x in os.getenv("AI_ALLOWED_HOSTS", "").split(",")
        if x.strip()
    }
    if configured_hosts and host not in configured_hosts:
        raise ValueError("AI endpoint host is not in AI_ALLOWED_HOSTS")
    if parsed.scheme != "https":
        if not (
            os.getenv("AI_ALLOW_INSECURE_LOCAL", "0") == "1"
            and host in {"127.0.0.1", "::1"}
            and os.getenv("APP_ENV", "development").lower() not in {"prod", "production", "staging"}
        ):
            raise ValueError("AI endpoint must use HTTPS")

    path = str(chat_path or "")
    if path and (not path.startswith("/") or "://" in path):
        raise ValueError("chat_path must be a relative path")
    return raw.rstrip("/")


def validate_rag_service_url(base_url: str, *, allow_loopback_http: bool = False) -> str:
    """Validate a user/configured RAG base URL before making an outbound call.

    RAG URLs can be supplied through a project mapping, so they are not
    trusted merely because they came from the database.  Resolve hostnames
    before the request and reject loopback, private, link-local, multicast,
    reserved, unspecified and cloud-metadata addresses.  Local HTTP is only
    permitted for the explicitly configured development loopback endpoint.
    Redirects are disabled by the callers; this check also rejects URL
    userinfo/query/fragment tricks that could hide the actual destination.
    """
    raw = str(base_url or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("RAG 服务地址必须是绝对 http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("RAG 服务地址不得包含用户信息、查询串或片段")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("RAG 服务端口无效") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("RAG 服务端口无效")

    host = parsed.hostname.rstrip(".").lower()
    if host in _BLOCKED_HOSTS:
        raise ValueError("RAG 服务地址命中了禁止的内部主机名")

    allowed_hosts = {
        item.strip().lower().rstrip(".")
        for item in os.getenv("TAX_RAG_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }
    if allowed_hosts and host not in allowed_hosts:
        raise ValueError("RAG 服务主机不在 TAX_RAG_ALLOWED_HOSTS 白名单中")

    try:
        literal = ipaddress.ip_address(host)
        addresses = {literal}
    except ValueError:
        try:
            infos = socket.getaddrinfo(
                host,
                port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError("RAG 服务主机无法解析") from exc
        addresses = set()
        for info in infos:
            sockaddr = info[4]
            if sockaddr:
                try:
                    addresses.add(ipaddress.ip_address(sockaddr[0]))
                except ValueError:
                    continue
        if not addresses:
            raise ValueError("RAG 服务主机没有可用地址")

    local_dev = (
        allow_loopback_http
        and parsed.scheme == "http"
        and os.getenv("APP_ENV", "development").strip().lower()
        not in {"prod", "production", "staging"}
        and all(address.is_loopback for address in addresses)
    )
    for address in addresses:
        blocked = (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )
        if blocked and not (local_dev and address.is_loopback):
            raise ValueError("RAG 服务地址不得指向内网、回环、链路本地或元数据地址")

    if parsed.scheme != "https" and not local_dev:
        raise ValueError("非本机 RAG 服务必须使用 HTTPS")
    return raw.rstrip("/")
