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

from fastapi import HTTPException, UploadFile, status
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
    # The browser login page and same-origin session bridge have their own
    # authentication policy.  They must be reachable before a browser has a
    # Tax JWT cookie; the session endpoint exchanges credentials server-side
    # and sets the HttpOnly cookie itself.
    "/login",
    "/api/v1/auth/session",
    # Logout is intentionally reachable without a valid session so that a
    # stale/expired browser cookie can still be cleared.  The route itself
    # enforces POST and same-origin CSRF checks.
    "/api/v1/auth/logout",
}

# The shared key is a service-to-service credential.  It is intentionally
# not an additional credential for user-facing routes that already validate a
# Tax-issued JWT and enforce RBAC at the route boundary.  Keep this list
# explicit: allowing every request carrying a valid JWT through this global
# middleware would accidentally expose legacy routes that do not yet declare
# a user-auth dependency of their own.
JWT_USER_PATH_PREFIXES = (
    "/api/v1/auth/verify",
    "/api/v1/executive",
    "/api/v1/retrieve/adaptive",
    "/api/v1/retrieve/deep",
    "/api/v1/retrieve/explain",
)

# These routes are called by the Tax/RAG bridge or an internal scheduler and
# must continue to require RAG_SHARED_API_KEY.  A Tax JWT must not be accepted
# as a substitute for the service credential on these paths.
SHARED_KEY_ONLY_PATH_PREFIXES = (
    "/api/v1/facts",
    "/api/v1/ai-review",
)

# Only these Web pages have a ``require_web_auth`` dependency in main.py.
# Keep the policy method-aware and segment-aware: a broad ``/projects/``
# prefix would also exempt upload/import mutation handlers that currently do
# not declare that dependency.  Those handlers must not gain an implicit
# authentication bypass from a browser cookie.
WEB_AUTH_EXACT_GET_PATHS = frozenset({
    "/",
    "/entities",
    "/health",
    "/search",
    "/regulations",
    "/mounts",
    "/api/v1/mounts/fs",
})
WEB_AUTH_DYNAMIC_GET_ROOTS = frozenset({"/projects", "/documents", "/entities"})

# Explicit user-facing mutation handlers.  This is deliberately an exact,
# method-aware allow-list: broad prefixes such as ``/projects/*`` or
# ``/api/v1/*`` would accidentally turn legacy/service endpoints into cookie
# routes before their own RBAC dependency is present.
WEB_AUTH_MUTATION_EXACT_PATHS = frozenset({
    "/projects",
    "/api/v1/regulations/verify",
    "/api/v1/regulations/save-custom",
    "/api/v1/regulations/ai-parse-url",
    "/api/v1/regulations/ai-parse-file",
    "/api/v1/mounts",
    "/api/v1/mounts/scan",
})


def _normalise_origin(value: str, *, allow_path: bool = False) -> str | None:
    """Return a canonical explicit HTTP(S) origin, or ``None``.

    This parser does not derive trust from ``Host`` or any forwarded header.
    ``Referer`` may include a path; ``Origin`` must contain only an origin.
    """
    raw = str(value or "").strip()
    if not raw or raw.lower() == "null" or "," in raw:
        return None
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (not allow_path and parsed.path not in {"", "/"})
    ):
        return None
    hostname = hostname.rstrip(".").lower()
    if not hostname:
        return None
    scheme = parsed.scheme.lower()
    default_port = 80 if scheme == "http" else 443
    port_suffix = "" if port is None or port == default_port else f":{port}"
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    return f"{scheme}://{host}{port_suffix}"


def _configured_csrf_origins() -> set[str]:
    """Read only explicitly configured browser origins."""
    raw_values = [os.getenv("RAG_CSRF_ALLOWED_ORIGINS", "")]
    if not raw_values[0].strip():
        raw_values.append(os.getenv("RAG_PUBLIC_ORIGIN", ""))
    origins: set[str] = set()
    for raw in raw_values:
        for candidate in raw.split(","):
            candidate = candidate.strip()
            if not candidate or candidate == "*":
                continue
            normalised = _normalise_origin(candidate)
            if normalised is not None:
                origins.add(normalised)
    return origins


def _default_loopback_csrf_origins() -> set[str]:
    """Return exact local origins for the RAG listener port."""
    raw_port = os.getenv("PROJECT_RAG_PORT", "8922").strip()
    try:
        port = int(raw_port)
    except ValueError:
        return set()
    if not 1 <= port <= 65535:
        return set()
    return {
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
        f"http://[::1]:{port}",
    }


def csrf_allowed_origins() -> set[str]:
    """Return the exact origins trusted for cookie-backed mutations."""
    return _default_loopback_csrf_origins() | _configured_csrf_origins()


def require_same_origin(request: Request) -> None:
    """Enforce fail-closed Origin-first, Referer-fallback CSRF protection.

    Browsers normally send ``Origin`` for POST/fetch requests.  If it is
    absent, a valid same-origin ``Referer`` is required.  ``X-Forwarded-*``
    headers are intentionally ignored; public proxy origins must be supplied
    through explicit server configuration.
    """
    origin = request.headers.get("origin")
    if origin is not None:
        candidate = _normalise_origin(origin)
        if candidate in csrf_allowed_origins():
            return
    else:
        referer = request.headers.get("referer")
        if referer is not None:
            candidate = _normalise_origin(referer, allow_path=True)
            if candidate in csrf_allowed_origins():
                return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="请求来源未通过同源校验",
    )


def _route_declares_web_auth(request: Request) -> bool:
    """Check the matched FastAPI route for an explicit Web auth dependency.

    ``RAGSecurityMiddleware`` runs before the router, so a path-only bypass
    would be unsafe: a separately mounted handler at the same path could
    receive a valid browser cookie without checking it.  FastAPI routes are
    available on ``request.app.router.routes`` after startup; inspect the
    route match and its flattened dependency graph before bypassing the
    service-key gate.
    """
    try:
        from starlette.routing import Match

        routes = request.app.router.routes
    except AttributeError:
        return False

    def _has_marker(dependant) -> bool:
        if getattr(getattr(dependant, "call", None), "__web_auth_boundary__", False):
            return True
        return any(_has_marker(child) for child in getattr(dependant, "dependencies", ()) or ())

    def _iter_routes(candidates):
        for route in candidates:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from _iter_routes(getattr(included, "routes", ()) or ())
            else:
                yield route

    for route in _iter_routes(routes):
        if not hasattr(route, "matches") or not hasattr(route, "dependant"):
            continue
        try:
            match, _ = route.matches(request.scope)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if match is Match.FULL and _has_marker(route.dependant):
            return True
    return False


def _is_web_auth_route(path: str, method: str) -> bool:
    """Return whether *path* is an explicitly Web-JWT-protected page.

    This mirrors the Web routes in ``app.main`` that actually use
    ``Depends(require_web_auth)``.  It intentionally does not treat every
    browser-looking route as authenticated: paths without that dependency
    remain behind the shared-key gate until their route-level auth is added.
    """
    if method.upper() != "GET":
        return False
    if path in WEB_AUTH_EXACT_GET_PATHS:
        return True

    segments = [segment for segment in path.split("/") if segment]
    if (
        len(segments) == 5
        and segments[:3] == ["api", "v1", "documents"]
        and segments[4] == "original"
    ):
        # Exact compatibility boundary for document original downloads.
        return True
    if len(segments) == 4 and segments[:3] == ["api", "v1", "regulations"]:
        # Exact compatibility boundary for regulation detail. Other
        # regulation API paths remain service-key-only.
        return segments[3].isdigit()
    if len(segments) == 2 and f"/{segments[0]}" in WEB_AUTH_DYNAMIC_GET_ROOTS:
        # /projects/{project_ref}, /documents/{document_id},
        # /entities/{entity_code}
        return True
    if len(segments) == 3 and segments[0] == "projects" and segments[2] == "audit":
        # /projects/{project_id}/audit
        return True
    return False


def _is_web_mutation_route(path: str, method: str) -> bool:
    """Return whether a method/path pair is an explicitly Web mutation.

    Only handlers that declare the cookie-session and CSRF dependencies are
    listed here.  A service/API route not listed remains behind the shared
    service key in the middleware, even if it happens to resemble a UI path.
    """
    if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if path in WEB_AUTH_MUTATION_EXACT_PATHS:
        return True
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) == 3 and segments[0] == "projects" and segments[2] in {"upload", "import-folder"}:
        return True
    if len(segments) == 3 and segments[0] == "documents" and segments[2] == "parse":
        return True
    if len(segments) == 5 and segments[:3] == ["api", "v1", "mounts"] and segments[4] == "delete":
        return True
    return False


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


def is_valid_shared_api_key(request: Request) -> bool:
    """Validate the configured service credential in constant time.

    This helper is used by dual-channel Web routes after the middleware. It
    intentionally accepts the same explicit ``Authorization: Bearer`` or
    ``X-API-Key`` forms as the middleware, and never logs or returns the key.
    """
    supplied = _provided_api_key(request)
    configured = str(RAG_SHARED_API_KEY or "")
    if not supplied or not configured:
        return False
    return hmac.compare_digest(supplied, configured)


def _path_has_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    """Match a route prefix without treating similarly named paths as equal."""
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def _valid_tax_jwt(request: Request, *, from_cookie: bool = False) -> bool:
    """Return whether the request carries a valid Tax access JWT.

    This is only used to decide whether the *global shared-key* check should
    be skipped for explicitly user-authenticated routes.  The route's own
    ``require_auth``/RBAC dependency remains authoritative and is still run.

    API routes use the Authorization header.  Explicit Web pages use only the
    HttpOnly ``cdjg_rag_token`` cookie; a cookie is never accepted as an API
    credential by this middleware.
    """
    if from_cookie:
        token = request.cookies.get("cdjg_rag_token", "").strip()
    else:
        authorization = request.headers.get("authorization", "")
        if not authorization.lower().startswith("bearer "):
            return False
        token = authorization[7:].strip()
    if not token:
        return False
    # Import lazily to keep security helpers usable during app bootstrap and
    # avoid making auth configuration part of module import ordering.
    from .auth import verify_tax_jwt

    return verify_tax_jwt(token) is not None


class RAGSecurityMiddleware(BaseHTTPMiddleware):
    """Fail closed for non-loopback deployments and protect unsafe routes."""

    async def dispatch(self, request: Request, call_next):
        # CORS preflight: let CORSMiddleware respond with 200 + ACAO headers.
        # OPTIONS requests carry no business data and no Authorization header
        # in browsers, so the security policy below would reject them and break
        # every cross-origin request. Let them pass through unchanged.
        if request.method == "OPTIONS":
            return await call_next(request)
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
            # Tax users authenticate at the endpoint with JWT + RBAC.  The
            # global service-key gate must not turn that into a second factor
            # for the explicit user route set.  Service-only routes always
            # take the shared-key branch, even when a valid JWT is present.
            api_jwt_ok = (
                _path_has_prefix(path, JWT_USER_PATH_PREFIXES)
                and not _path_has_prefix(path, SHARED_KEY_ONLY_PATH_PREFIXES)
                and _valid_tax_jwt(request)
            )
            web_route_candidate = _is_web_auth_route(path, request.method) or _is_web_mutation_route(
                path,
                request.method,
            )
            web_route = web_route_candidate and _route_declares_web_auth(request)
            web_cookie_ok = web_route and _valid_tax_jwt(request, from_cookie=True)
            # A protected Web GET must reach ``require_web_auth`` even when
            # its cookie is missing/expired, so the route can return the
            # intended 302 /login response.  Mutations do not receive this
            # exception: they still require a valid cookie (or, for legacy
            # callers, the independent service key) before route-level 401.
            web_get_reaches_route = web_route and request.method.upper() == "GET"
            user_jwt_ok = api_jwt_ok or web_cookie_ok or web_get_reaches_route
            if not user_jwt_ok:
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
    if ext not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".html"}:
        raise ValueError(f"unsupported file type: {ext or '(none)'}")
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
