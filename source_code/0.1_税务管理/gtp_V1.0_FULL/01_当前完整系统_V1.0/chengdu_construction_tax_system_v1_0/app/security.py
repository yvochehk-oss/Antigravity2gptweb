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
from collections.abc import Collection
from urllib.parse import unquote, urlsplit

_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata",
    "instance-data",
}
_LOCAL_HOSTS = {"localhost", "localhost.localdomain"}
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

# ``AI_ALLOW_PRIVATE_LLM`` is intentionally separate from the RAG outbound
# policy.  Tax is deployed inside the company network and may call a local or
# RFC1918 model over HTTP, but that exception must never become a general
# internal-URL bypass for document/RAG fetching.
_PRIVATE_LLM_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # Permit the IPv6 equivalent of an internal RFC1918 deployment while
    # retaining the same special-range blocks below.
    ipaddress.ip_network("fc00::/7"),
)


def private_llm_allowed() -> bool:
    """Whether the explicitly configured internal-LLM exception is active."""
    return os.getenv("AI_ALLOW_PRIVATE_LLM", "0").strip().lower() in _TRUE_VALUES


def _is_private_llm_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Return whether an address is an allowed loopback/private LLM target."""
    return address.is_loopback or any(
        address in network for network in _PRIVATE_LLM_NETWORKS
    )


def _validate_path(path: str, *, field_name: str) -> None:
    """Reject path forms that can escape the configured AI origin."""
    if not path:
        return
    decoded_path = unquote(path)
    if not path.startswith("/"):
        raise ValueError(f"{field_name} must start with '/'")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded_path):
        raise ValueError(f"{field_name} contains control characters")
    if "\\" in decoded_path or "://" in decoded_path:
        raise ValueError(f"{field_name} contains an unsafe URL form")
    path_parts = urlsplit(decoded_path)
    if path_parts.scheme or path_parts.netloc or path_parts.query or path_parts.fragment:
        raise ValueError(f"{field_name} must be a path without query or fragment")
    if any(part in {".", ".."} for part in decoded_path.split("/")):
        raise ValueError(f"{field_name} must not contain dot segments")


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
    loopback/RFC1918 endpoint is allowed over HTTP or HTTPS only when the
    explicit internal-deployment policy ``AI_ALLOW_PRIVATE_LLM=1`` is active.
    Link-local, metadata, multicast, reserved and unspecified targets are
    always rejected.  This helper is only for Tax AI endpoints; the RAG URL
    validator below deliberately keeps its stricter policy.
    """
    raw = str(base_url or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("AI base_url must be an absolute http(s) URL")
    # Checking the parsed username/password is not enough for ``http://@host``:
    # urllib treats an empty userinfo section as ``None``.  Reject the marker
    # itself so every userinfo form is denied before the URL is handed to an
    # HTTP client.
    if (
        "@" in parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "AI base_url must not contain userinfo, query string or fragment"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("AI base_url port is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("AI base_url port is invalid")

    host = parsed.hostname.rstrip(".").lower()
    if host in _BLOCKED_HOSTS:
        raise ValueError("AI endpoint host is not allowed")
    # An IPv6 zone identifier is not a routable model host and can otherwise
    # evade ``ipaddress.ip_address`` parsing (for example ``fe80::1%25en0``).
    if "%" in host:
        raise ValueError("AI endpoint host must not contain an IPv6 zone identifier")
    private_policy = private_llm_allowed()
    if host in _LOCAL_HOSTS and not private_policy:
        raise ValueError(
            "loopback AI endpoints require AI_ALLOW_PRIVATE_LLM=1"
        )

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        # These ranges are never valid model destinations, even for the
        # internal deployment policy.  In particular, allowing all
        # ``is_private`` addresses would accidentally allow link-local and
        # cloud metadata services.
        if (
            address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError(
                "AI endpoint must not target link-local, metadata, multicast, "
                "reserved or unspecified IPs"
            )
        if _is_private_llm_address(address) and not private_policy:
            raise ValueError(
                "private/loopback AI endpoints require AI_ALLOW_PRIVATE_LLM=1"
            )
        # Other private/special ranges (for example, carrier-grade NAT or
        # documentation space) are not RFC1918 model targets and remain
        # blocked rather than being treated as public internet hosts.
        if address.is_private and not _is_private_llm_address(address):
            raise ValueError("AI endpoint must not target a private IP range")

    configured_hosts = {
        x.strip().lower().rstrip(".")
        for x in os.getenv("AI_ALLOWED_HOSTS", "").split(",")
        if x.strip()
    }
    local_or_private = (
        host in _LOCAL_HOSTS
        or (address is not None and _is_private_llm_address(address))
    )
    # ``AI_ALLOWED_HOSTS`` governs public integrations.  An explicitly
    # enabled internal deployment must remain usable for a local/RFC1918 LLM
    # even when the same allow-list contains only the public provider host.
    if configured_hosts and not local_or_private and host not in configured_hosts:
        raise ValueError("AI endpoint host is not in AI_ALLOWED_HOSTS")
    if parsed.scheme != "https" and not (private_policy and local_or_private):
        raise ValueError("public AI endpoints must use HTTPS")

    _validate_path(str(chat_path or "").strip(), field_name="chat_path")
    _validate_path(parsed.path, field_name="AI base_url path")
    return raw.rstrip("/")


def _rag_host_addresses(host: str, port: int | None, scheme: str) -> frozenset[str]:
    """Resolve one RAG host into canonical IP strings.

    The result is used both for the initial approval and for every later
    request.  Keeping resolution in one helper prevents the approval path and
    the request path from applying subtly different DNS rules.
    """
    try:
        literal = ipaddress.ip_address(host)
        return frozenset({str(literal)})
    except ValueError:
        try:
            infos = socket.getaddrinfo(
                host,
                port or (443 if scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError("RAG 服务主机无法解析") from exc
        addresses: set[str] = set()
        for info in infos:
            sockaddr = info[4]
            if sockaddr:
                try:
                    addresses.add(str(ipaddress.ip_address(sockaddr[0])))
                except ValueError:
                    continue
        if not addresses:
            raise ValueError("RAG 服务主机没有可用地址") from None
        return frozenset(addresses)


def resolve_rag_service_addresses(base_url: str) -> frozenset[str]:
    """Resolve a validated RAG URL for persistence of its approval snapshot."""
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("RAG 服务地址必须是绝对 http(s) URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("RAG 服务端口无效") from exc
    return _rag_host_addresses(parsed.hostname.rstrip(".").lower(), port, parsed.scheme)


def _rag_address_is_forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether an address is never a valid RAG destination."""
    return bool(
        address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_rag_service_url(
    base_url: str,
    *,
    allow_loopback_http: bool = False,
    allow_approved_private: bool = False,
    approved_addresses: Collection[str] | None = None,
) -> str:
    """Validate a user/configured RAG base URL before making an outbound call.

    RAG URLs can be supplied through a project mapping, so they are not
    trusted merely because they came from the database.  Resolve hostnames
    before the request and reject loopback, private, link-local, multicast,
    reserved, unspecified and cloud-metadata addresses.  Local HTTP is only
    permitted for the explicitly configured development loopback endpoint.
    A private/loopback destination is additionally allowed only when the
    caller presents the separately persisted, administrator-approved endpoint
    policy.  For hostname targets, ``approved_addresses`` is mandatory on
    subsequent requests and must exactly match the fresh DNS result; this is
    the DNS-rebinding/TOCTOU guard.  Redirects are disabled by the callers;
    this check also rejects URL userinfo/query/fragment tricks that could hide
    the actual destination.
    """
    raw = str(base_url or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("RAG 服务地址必须是绝对 http(s) URL")
    if (
        "@" in parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
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
    if "%" in host:
        raise ValueError("RAG 服务主机不得包含 IPv6 区域标识")

    address_strings = _rag_host_addresses(host, port, parsed.scheme)
    try:
        addresses = {ipaddress.ip_address(value) for value in address_strings}
    except ValueError as exc:  # pragma: no cover - helper already canonicalizes values
        raise ValueError("RAG 服务主机地址无效") from exc

    if approved_addresses is not None:
        expected_addresses = frozenset(
            str(ipaddress.ip_address(str(value).strip()))
            for value in approved_addresses
            if str(value).strip()
        )
        if not expected_addresses:
            raise ValueError("RAG 私有服务批准记录缺少地址快照")
        if address_strings != expected_addresses:
            raise ValueError("RAG 服务主机 DNS 解析已变化，需管理员重新批准")

    allowed_hosts = {
        item.strip().lower().rstrip(".")
        for item in os.getenv("TAX_RAG_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }
    private_approved_target = allow_approved_private and all(
        address.is_private or address.is_loopback for address in addresses
    )
    if allowed_hosts and host not in allowed_hosts and not private_approved_target:
        raise ValueError("RAG 服务主机不在 TAX_RAG_ALLOWED_HOSTS 白名单中")

    local_dev = (
        allow_loopback_http
        and parsed.scheme == "http"
        and os.getenv("APP_ENV", "development").strip().lower()
        not in {"prod", "production", "staging"}
        and all(address.is_loopback for address in addresses)
    )
    for address in addresses:
        if _rag_address_is_forbidden(address):
            raise ValueError("RAG 服务地址不得指向链路本地、组播、保留或未指定地址")
        private_or_loopback = address.is_private or address.is_loopback
        if private_or_loopback and not (
            (local_dev and address.is_loopback) or allow_approved_private
        ):
            raise ValueError("RAG 私有/回环服务地址必须经过管理员明确批准")

    if parsed.scheme != "https" and not (local_dev or allow_approved_private and any(
        address.is_private or address.is_loopback for address in addresses
    )):
        raise ValueError("非本机 RAG 服务必须使用 HTTPS")
    return raw.rstrip("/")
