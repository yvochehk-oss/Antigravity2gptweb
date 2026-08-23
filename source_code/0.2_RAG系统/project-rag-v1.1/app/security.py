"""Authentication, upload limits and security audit helpers for ProjectRAG."""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import UploadFile
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import AUTH_REQUIRED, MAX_UPLOAD_SIZE, RAG_SHARED_API_KEY
from .logging_config import get_logger

logger = get_logger(__name__)

PUBLIC_PATHS = {
    "/api/v1/health",
    "/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def _client_ip(request: Request) -> str:
    # Do not trust X-Forwarded-For unless a trusted proxy has already
    # normalized it.  This service's default deployment is loopback-only.
    return request.client.host if request.client else "unknown"


def security_audit(request: Request | None, event: str, outcome: str, **fields) -> None:
    """Emit a structured security event without tokens or request bodies."""
    payload = {
        "event": event,
        "outcome": outcome,
        "ip": _client_ip(request) if request else "system",
        "method": request.method if request else "",
        "path": request.url.path if request else "",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    for key in ("document_id", "project_id", "subject"):
        if key in fields and fields[key] is not None:
            payload[key] = str(fields[key])[:120]
    logger.info("security_event %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _provided_api_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


class RAGSecurityMiddleware(BaseHTTPMiddleware):
    """Fail closed for non-loopback deployments and protect unsafe routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        if AUTH_REQUIRED:
            if not RAG_SHARED_API_KEY:
                security_audit(request, "api_auth", "misconfigured")
                return JSONResponse(
                    {"detail": "RAG_SHARED_API_KEY 未配置，服务拒绝处理受保护请求"},
                    status_code=503,
                )
            supplied = _provided_api_key(request)
            if not supplied or not hmac.compare_digest(supplied, RAG_SHARED_API_KEY):
                security_audit(request, "api_auth", "denied")
                response = JSONResponse({"detail": "missing or invalid API key"}, status_code=401)
                response.headers["WWW-Authenticate"] = "Bearer"
                return response

        # A configured key also protects browser pages and mutation routes;
        # callers can use the same bearer token for the UI/API boundary.
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


def _extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def validate_file_content(filename: str, data: bytes) -> None:
    """Validate size, extension and common file signatures before storage."""
    if len(data) > MAX_UPLOAD_SIZE:
        raise ValueError(f"file exceeds maximum size of {MAX_UPLOAD_SIZE} bytes")
    if not filename or "\x00" in filename:
        raise ValueError("invalid filename")
    ext = _extension(filename)
    signatures = {
        ".pdf": lambda b: b.startswith(b"%PDF-"),
        ".png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": lambda b: b.startswith(b"\xff\xd8\xff"),
        ".jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
        ".webp": lambda b: b.startswith(b"RIFF") and b[8:12] == b"WEBP",
        ".docx": lambda b: b.startswith(b"PK\x03\x04"),
        ".xlsx": lambda b: b.startswith(b"PK\x03\x04"),
        ".pptx": lambda b: b.startswith(b"PK\x03\x04"),
    }
    check = signatures.get(ext)
    if check and not check(data[:16]):
        raise ValueError(f"file content does not match {ext} signature")
    if ext in {".docx", ".xlsx", ".pptx"}:
        _validate_zip_container(data)


def _validate_zip_container(data: bytes) -> None:
    """Reject obvious archive bombs without extracting user content."""
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 1000:
                raise ValueError("archive contains too many entries")
            total = 0
            for member in members:
                if member.filename.startswith(("/", "\\")) or ".." in Path(member.filename).parts:
                    raise ValueError("archive contains a path traversal entry")
                total += max(0, member.file_size)
                if total > MAX_UPLOAD_SIZE * 4:
                    raise ValueError("archive expands beyond the allowed size")
                if member.compress_size and member.file_size / member.compress_size > 1000:
                    raise ValueError("archive compression ratio is unsafe")
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid zip container") from exc


async def read_upload_limited(file: UploadFile) -> bytes:
    """Read an upload in bounded chunks, rejecting oversized bodies early."""
    chunks: list[bytes] = []
    total = 0
    chunk_size = 1024 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_SIZE:
            raise ValueError(f"file exceeds maximum size of {MAX_UPLOAD_SIZE} bytes")
        chunks.append(chunk)
    data = b"".join(chunks)
    validate_file_content(file.filename or "", data)
    return data


def read_file_limited(path: Path) -> bytes:
    """Read a local import in bounded chunks rather than ``read_bytes()``."""
    chunks: list[bytes] = []
    total = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_SIZE:
                raise ValueError(f"file exceeds maximum size of {MAX_UPLOAD_SIZE} bytes")
            chunks.append(chunk)
    data = b"".join(chunks)
    validate_file_content(path.name, data)
    return data


def validate_outbound_url(url: str, *, require_https: bool = True) -> str:
    """Reject private/loopback/metadata destinations for outbound HTTP."""
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("outbound URL must be an absolute http(s) URL")
    if require_https and parsed.scheme != "https":
        raise ValueError("outbound URL must use HTTPS")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "metadata", "metadata.google.internal", "instance-data"}:
        raise ValueError("private metadata host is not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError("outbound host cannot be resolved") from exc
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("outbound URL resolves to a private or reserved address")
    return str(url).rstrip("/")


# LLM endpoints are a deliberate exception to the generic outbound policy:
# Ollama, LM Studio and vLLM are commonly bound to loopback or an RFC1918
# address.  Keep this policy separate from ``validate_outbound_url`` so that
# Facts/import/bridge URLs cannot accidentally inherit the LLM exception.
_LLM_METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_LLM_ALLOWED_PRIVATE_V4 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_LLM_DOCUMENTATION_NETWORKS = (
    # IPv4 documentation, benchmarking and special-purpose ranges.
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / shared address space
    # IPv6 documentation, benchmarking and ULA.  ULA is intentionally not
    # opened in this release; IPv6 loopback remains allowed below.
    ipaddress.ip_network("2001:db8::/32"),
    ipaddress.ip_network("2001:2::/48"),
    ipaddress.ip_network("fc00::/7"),
)


def _llm_effective_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse an address and normalize IPv4-mapped IPv6 for classification."""
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ValueError("outbound LLM host resolved to an invalid IP address") from exc
    # ``::ffff:127.0.0.1`` and ``::ffff:10.x`` are semantically IPv4
    # destinations.  Classify the mapped value so they cannot bypass the
    # RFC1918/loopback rules.
    if isinstance(parsed, ipaddress.IPv6Address):
        return parsed.ipv4_mapped or parsed
    return parsed


def _classify_llm_ip(raw: str) -> str:
    """Classify a resolved address as allowed private or public.

    The only private IPv4 ranges opened by this policy are loopback and
    RFC1918.  IPv6 ULA, link-local, special-purpose and non-global ranges are
    intentionally rejected; ``::1`` and IPv4-mapped loopback/RFC1918 remain
    supported for local model servers.
    """
    ip = _llm_effective_ip(raw)

    # Some Python versions expose IPv6 loopback as ``is_reserved``; it is an
    # explicitly supported local LLM destination and must be handled first.
    if ip.is_loopback:
        return "private"
    if ip.is_unspecified or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise ValueError("outbound LLM URL resolves to an unsafe special address")
    if any(ip in network for network in _LLM_DOCUMENTATION_NETWORKS):
        raise ValueError("outbound LLM URL resolves to a reserved or special-purpose address")

    if ip.version == 4 and any(ip in network for network in _LLM_ALLOWED_PRIVATE_V4):
        return "private"

    # This also rejects IPv6 ULA and other private/site-local values that are
    # not part of the explicitly supported local LLM surface.
    if ip.is_private:
        raise ValueError("outbound LLM URL resolves to an unsupported private address")
    # ``100.64.0.0/10`` is neither ``is_private`` nor ``is_global`` in the
    # stdlib, and is handled above for a deterministic CGNAT rejection.
    if not ip.is_global:
        raise ValueError("outbound LLM URL resolves to a non-global address")
    return "public"


def validate_llm_outbound_url(url: str) -> str:
    """Validate an OpenAI-compatible LLM endpoint.

    Public endpoints must use HTTPS.  Local/internal model servers may use
    HTTP or HTTPS, but every resolved address must be an explicitly allowed
    loopback/RFC1918 address; a DNS name resolving to both private and public
    addresses is rejected.  This function is intentionally not used for the
    generic Facts/import/bridge outbound boundary.
    """
    raw_url = str(url or "").strip()
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("outbound LLM URL has an invalid host or port") from exc

    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError("outbound LLM URL must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("outbound LLM URL must not contain userinfo")

    host = hostname.rstrip(".").lower()
    if host in _LLM_METADATA_HOSTS:
        raise ValueError("metadata host is not allowed for LLM outbound requests")

    # Use the URL's explicit port, or the scheme's normal default.  Passing
    # the actual port matters for DNS answers and avoids treating HTTP as
    # HTTPS merely because the old generic helper always used 443.
    resolve_port = port if port is not None else (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, resolve_port, type=socket.SOCK_STREAM)
        addresses = {str(item[4][0]) for item in infos if item and item[4]}
    except (OSError, ValueError) as exc:
        raise ValueError("outbound LLM host cannot be resolved") from exc
    if not addresses:
        raise ValueError("outbound LLM host cannot be resolved")

    classifications = {_classify_llm_ip(address) for address in addresses}
    if len(classifications) != 1:
        raise ValueError("outbound LLM host resolves to mixed private and public addresses")
    classification = classifications.pop()
    if classification == "public" and parsed.scheme != "https":
        raise ValueError("public LLM endpoints must use HTTPS")
    return raw_url.rstrip("/")
