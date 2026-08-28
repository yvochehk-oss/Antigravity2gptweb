"""Shared, fail-closed LLM endpoint pool for ProjectRAG.

The RAG service owns a small endpoint table, while older installations still
have their one endpoint in environment variables.  This module is the only
place where an LLM endpoint is selected and called.  In particular:

* database rows are ordered deterministically by ``priority, id``;
* an existing (even disabled) database row suppresses the legacy fallback;
* every resolved URL is checked by the LLM-specific SSRF policy before an
  HTTP client is constructed;
* failures and empty completions move to the next endpoint; and
* returned metadata never contains an API key.

The application-facing helpers intentionally return a small result object so
callers can retain the effective endpoint and attempt trail without exposing
credentials.  They may still use ``result.text`` where the old services only
need the generated text.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import select

from ..config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_LOCAL_BASE_URL,
    LLM_LOCAL_MODEL,
    LLM_LOCAL_TIMEOUT_SECONDS,
    LLM_MODEL,
)
from ..db import SessionLocal
from ..logging_config import get_logger
from ..models import LLMModelEndpoint
from ..security import validate_llm_outbound_url

logger = get_logger(__name__)

DEFAULT_CHAT_PATH = "/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_LOCAL_TIMEOUT_SECONDS = 60
DEFAULT_ROUTING_GROUP = "default"
DEFAULT_LOCAL_ENDPOINT_NAME = "local-llama.cpp"


class LLMPoolError(Exception):
    """Controlled error raised when no configured endpoint can answer."""

    def __init__(
        self,
        message: str,
        *,
        attempts: Iterable[Mapping[str, Any]] = (),
        catalog: "EndpointCatalog | None" = None,
    ) -> None:
        # Do not put raw provider errors in the exception.  Some HTTP client
        # implementations include request headers or response bodies in their
        # exception text; the API key must not reach logs or API responses.
        self.attempts = [dict(item) for item in attempts]
        self.catalog = catalog
        super().__init__(message)


@dataclass(frozen=True)
class EndpointConfig:
    """A server-side endpoint configuration.

    ``api_key`` is intentionally present only in this private object.  Use
    :func:`safe_endpoint_view` for logs, status responses, or attempt trails.
    """

    id: int | None
    name: str
    base_url: str
    chat_path: str
    model: str
    api_key: str = ""
    enabled: bool = True
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    priority: int = 100
    routing_group: str = DEFAULT_ROUTING_GROUP
    source: str = "database"
    fallback_reason: str = ""


# A shorter name is convenient for callers and preserves compatibility with
# tests/integrations that refer to an endpoint rather than its config.
LLMEndpoint = EndpointConfig


@dataclass(frozen=True)
class EndpointCatalog:
    """Resolved endpoint list plus explicit database/fallback state."""

    endpoints: tuple[EndpointConfig, ...] = ()
    table_state: str = "unknown"
    row_count: int | None = None
    source: str = "none"
    error_class: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.endpoints)


@dataclass
class LLMCallResult:
    """Text plus key-free routing evidence for one successful call."""

    text: str
    attempts: list[dict[str, Any]] = field(default_factory=list)
    effective_endpoint: dict[str, Any] = field(default_factory=dict)

    @property
    def content(self) -> str:
        """Alias useful to adapters that call the result ``content``."""
        return self.text

    @property
    def endpoint(self) -> dict[str, Any]:
        """Backward/ergonomic alias for the effective endpoint view."""
        return self.effective_endpoint

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe, credential-free representation."""
        return {
            "text": self.text,
            "attempts": [dict(item) for item in self.attempts],
            "effective_endpoint": dict(self.effective_endpoint),
        }


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    """Read a model/fake row without assuming SQLAlchemy instrumentation."""
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _integer(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rows_from_result(result: Any) -> list[Any]:
    """Extract ORM rows from real or deliberately small fake results."""
    if result is None:
        return []
    if hasattr(result, "scalars"):
        scalars = result.scalars()
        if hasattr(scalars, "all"):
            return list(scalars.all())
        return list(scalars)
    if hasattr(result, "all"):
        values = list(result.all())
    elif isinstance(result, (list, tuple, set)):
        values = list(result)
    else:
        try:
            values = list(result)
        except TypeError:
            values = [result]

    # Some fake sessions return ``[(row,), ...]`` for execute().
    unwrapped: list[Any] = []
    for value in values:
        if isinstance(value, (tuple, list)) and len(value) == 1:
            unwrapped.append(value[0])
        else:
            unwrapped.append(value)
    return unwrapped


def _read_rows(session: Any) -> list[Any]:
    """Read all rows so disabled/non-default rows suppress env fallback."""
    statement = select(LLMModelEndpoint)
    if hasattr(session, "execute"):
        return _rows_from_result(session.execute(statement))
    if hasattr(session, "query"):
        return list(session.query(LLMModelEndpoint).all())
    raise TypeError("LLM endpoint session does not provide execute/query")


def _is_missing_table_error(exc: BaseException) -> bool:
    """Recognize only an actually missing endpoint table.

    The legacy environment endpoint is a migration compatibility path, not a
    database-outage fallback.  SQLAlchemy commonly wraps PostgreSQL's
    ``UndefinedTable`` in ``ProgrammingError`` and SQLite-compatible test
    doubles usually only expose a short ``no such table`` message.  Walk the
    exception chain and require the endpoint table name in textual fallbacks;
    a generic ``does not exist`` or permission/connection error must not be
    mistaken for a missing table.
    """
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)

        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if str(sqlstate or "") == "42P01":
            return True
        if current.__class__.__name__.lower() in {"undefinedtable", "nosuchtable"}:
            return True

        message = str(current).lower()
        if (
            "rag_llm_model_endpoints" in message
            and (
                "no such table" in message
                or "undefined table" in message
                or "does not exist" in message
            )
        ):
            return True

        for relation in (
            getattr(current, "orig", None),
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
        ):
            if isinstance(relation, BaseException):
                pending.append(relation)
    return False


def _safe_url_for_view(raw_url: str) -> str:
    """Strip credentials/query material before exposing an endpoint URL."""
    try:
        parsed = urlsplit(str(raw_url or "").strip())
        if not parsed.scheme or not parsed.hostname:
            return ""
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = ""
        with suppress(ValueError):
            if parsed.port is not None:
                default = 80 if parsed.scheme == "http" else 443
                if parsed.port != default:
                    port = f":{parsed.port}"
        return urlunsplit((parsed.scheme.lower(), f"{host}{port}", parsed.path, "", "")).rstrip("/")
    except (TypeError, ValueError):
        return ""


def safe_endpoint_view(endpoint: EndpointConfig | Any) -> dict[str, Any]:
    """Return endpoint metadata safe for API/logging use.

    This function deliberately has an allow-list.  In particular, it does
    not copy arbitrary row attributes and never includes ``api_key``.
    """
    return {
        "id": _row_value(endpoint, "id"),
        "name": str(_row_value(endpoint, "name", "") or ""),
        "base_url": _safe_url_for_view(str(_row_value(endpoint, "base_url", "") or "")),
        "chat_path": str(_row_value(endpoint, "chat_path", DEFAULT_CHAT_PATH) or DEFAULT_CHAT_PATH),
        "model": str(_row_value(endpoint, "model", "") or ""),
        "enabled": bool(_row_value(endpoint, "enabled", True)),
        "timeout_seconds": _integer(_row_value(endpoint, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS), DEFAULT_TIMEOUT_SECONDS),
        "priority": _integer(_row_value(endpoint, "priority", 100), 100),
        "routing_group": str(_row_value(endpoint, "routing_group", DEFAULT_ROUTING_GROUP) or DEFAULT_ROUTING_GROUP),
        "source": str(_row_value(endpoint, "source", "database") or "database"),
    }


def _endpoint_from_row(row: Any) -> EndpointConfig:
    """Convert a database row into the private endpoint representation."""
    return EndpointConfig(
        id=_row_value(row, "id"),
        name=str(_row_value(row, "name", "") or ""),
        base_url=str(_row_value(row, "base_url", "") or "").strip(),
        chat_path=str(_row_value(row, "chat_path", DEFAULT_CHAT_PATH) or DEFAULT_CHAT_PATH).strip(),
        model=str(_row_value(row, "model", "") or "").strip(),
        api_key=str(_row_value(row, "api_key", "") or ""),
        enabled=bool(_row_value(row, "enabled", True)),
        timeout_seconds=max(1, min(_integer(_row_value(row, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS), DEFAULT_TIMEOUT_SECONDS), 600)),
        priority=max(0, _integer(_row_value(row, "priority", 100), 100)),
        routing_group=str(_row_value(row, "routing_group", DEFAULT_ROUTING_GROUP) or DEFAULT_ROUTING_GROUP).strip(),
        source="database",
    )


def _legacy_endpoint(
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    timeout_seconds: int,
    fallback_reason: str,
) -> EndpointConfig | None:
    """Build an explicit legacy endpoint only when both required values exist."""
    resolved_base = str(LLM_BASE_URL if base_url is None else base_url or "").strip()
    resolved_model = str(LLM_MODEL if model is None else model or "").strip()
    if not resolved_base or not resolved_model:
        return None
    return EndpointConfig(
        id=None,
        name="legacy-env",
        base_url=resolved_base,
        chat_path=DEFAULT_CHAT_PATH,
        model=resolved_model,
        api_key=str(LLM_API_KEY if api_key is None else api_key or ""),
        enabled=True,
        timeout_seconds=max(1, min(_integer(timeout_seconds, DEFAULT_TIMEOUT_SECONDS), 600)),
        priority=100,
        routing_group=DEFAULT_ROUTING_GROUP,
        source="legacy_env",
        fallback_reason=fallback_reason,
    )


def _local_fallback_endpoint(
    *,
    base_url: str | None,
    model: str | None,
    timeout_seconds: int | None,
) -> EndpointConfig | None:
    """Build the non-persistent endpoint injected by the root launcher.

    ``None`` means "use the process configuration" while an empty string is
    an explicit disable value.  The local endpoint never accepts an API key:
    the managed llama.cpp server is a loopback process and this fallback must
    not become another place where credentials are stored or propagated.
    """
    resolved_base = str(LLM_LOCAL_BASE_URL if base_url is None else base_url or "").strip()
    resolved_model = str(LLM_LOCAL_MODEL if model is None else model or "").strip()
    if not resolved_base or not resolved_model:
        return None
    resolved_timeout = (
        LLM_LOCAL_TIMEOUT_SECONDS
        if timeout_seconds is None
        else _integer(timeout_seconds, DEFAULT_LOCAL_TIMEOUT_SECONDS)
    )
    return EndpointConfig(
        id=None,
        name=DEFAULT_LOCAL_ENDPOINT_NAME,
        base_url=resolved_base,
        chat_path=DEFAULT_CHAT_PATH,
        model=resolved_model,
        api_key="",
        enabled=True,
        timeout_seconds=max(1, min(resolved_timeout, 600)),
        # This is metadata only.  The pool appends the fallback after all
        # database/legacy candidates instead of relying on a magic priority.
        priority=10000,
        routing_group=DEFAULT_ROUTING_GROUP,
        source="local_fallback",
        fallback_reason="managed_local_llm",
    )


def get_endpoint_catalog(
    *,
    session: Any = None,
    routing_group: str = DEFAULT_ROUTING_GROUP,
    legacy_base_url: str | None = None,
    legacy_model: str | None = None,
    legacy_api_key: str | None = None,
    legacy_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    local_base_url: str | None = None,
    local_model: str | None = None,
    local_timeout_seconds: int | None = None,
) -> EndpointCatalog:
    """Resolve the configured endpoint list.

    All rows are read before filtering.  Thus a table containing only a
    disabled row, or rows belonging to another routing group, is a deliberate
    configured state and does not silently reactivate the legacy environment
    endpoint.  A healthy local llama.cpp process is a separate, non-persistent
    last fallback: when configured, it is appended after every eligible user
    endpoint, including when the migrated table is empty.  A
    missing/unavailable table is an explicit migration compatibility state; it
    may use the legacy endpoint, but only if the legacy configuration is
    complete.  Missing configuration still produces an empty catalog.
    """
    owns_session = session is None
    db_session = session
    table_state = "available"
    row_count: int | None = None
    error_class = ""

    try:
        if owns_session:
            db_session = SessionLocal()
        rows = _read_rows(db_session)
        row_count = len(rows)
    except Exception as exc:
        error_class = exc.__class__.__name__
        rows = []
        if _is_missing_table_error(exc):
            # Only a not-yet-migrated installation may use the old env
            # endpoint.  Keep this state distinct in logs and metadata.
            table_state = "unavailable"
            logger.warning(
                "RAG LLM endpoint table is missing; using controlled legacy compatibility: %s",
                error_class,
            )
        else:
            # Connection, permission, transaction, and unexpected database
            # errors are fail-closed.  Re-enabling an env model here could
            # bypass an explicitly disabled database configuration and make a
            # production outage look healthy.
            table_state = "error"
            logger.error(
                "RAG LLM endpoint table lookup failed; refusing legacy fallback: %s",
                error_class,
            )
    finally:
        if owns_session and db_session is not None and hasattr(db_session, "close"):
            with suppress(Exception):
                db_session.close()

    local_fallback = _local_fallback_endpoint(
        base_url=local_base_url,
        model=local_model,
        timeout_seconds=local_timeout_seconds,
    )

    legacy_endpoint = _legacy_endpoint(
        base_url=legacy_base_url,
        model=legacy_model,
        api_key=legacy_api_key,
        timeout_seconds=legacy_timeout_seconds,
        fallback_reason="empty_table_env_fallback",
    )

    if table_state == "available" and row_count == 0:
        candidates = []
        if legacy_endpoint:
            candidates.append(legacy_endpoint)
        if local_fallback:
            candidates.append(local_fallback)
        return EndpointCatalog(
            endpoints=tuple(candidates),
            table_state=table_state,
            row_count=row_count,
            source="legacy_env_fallback" if legacy_endpoint else ("database_with_local_fallback" if local_fallback else "database_configured_empty"),
        )

    if table_state == "available":
        eligible = [
            _endpoint_from_row(row)
            for row in rows
            if bool(_row_value(row, "enabled", True))
            and str(_row_value(row, "routing_group", DEFAULT_ROUTING_GROUP) or DEFAULT_ROUTING_GROUP).strip()
            == routing_group
        ]
        eligible.sort(key=lambda endpoint: (endpoint.priority, _integer(endpoint.id, 0)))
        if local_fallback and routing_group == DEFAULT_ROUTING_GROUP:
            # Any persisted local row, even a disabled one, is an explicit
            # operator choice.  Do not silently reactivate it through the
            # launcher-provided fallback after an admin disables it.
            if not any(_same_endpoint(row, local_fallback) for row in rows):
                eligible.append(local_fallback)
        source = "database_with_local_fallback" if local_fallback and eligible and eligible[-1] is local_fallback else (
            "database" if eligible else "database_configured_empty"
        )
        return EndpointCatalog(
            endpoints=tuple(eligible),
            table_state=table_state,
            row_count=row_count,
            source=source,
        )

    if table_state == "error":
        return EndpointCatalog(
            endpoints=(),
            table_state=table_state,
            row_count=row_count,
            source="database_error",
            error_class=error_class,
        )

    # A table that is not present cannot express a disabled row.  Keep this
    # branch visibly distinct from the zero-row branch in catalog metadata.
    legacy = _legacy_endpoint(
        base_url=legacy_base_url,
        model=legacy_model,
        api_key=legacy_api_key,
        timeout_seconds=legacy_timeout_seconds,
        fallback_reason="endpoint_table_unavailable",
    )
    endpoints: list[EndpointConfig] = []
    if legacy:
        endpoints.append(legacy)
    if local_fallback and routing_group == DEFAULT_ROUTING_GROUP:
        if not any(_same_endpoint(endpoint, local_fallback) for endpoint in endpoints):
            endpoints.append(local_fallback)
    if legacy and local_fallback:
        source = "legacy_env_compat_with_local_fallback"
    elif legacy:
        source = "legacy_env_compat"
    elif local_fallback:
        source = "local_fallback"
    else:
        source = "none"
    return EndpointCatalog(
        endpoints=tuple(endpoints),
        table_state=table_state,
        row_count=row_count,
        source=source,
        error_class=error_class,
    )


def load_endpoints(**kwargs: Any) -> list[EndpointConfig]:
    """Compatibility helper returning only the resolved endpoint list."""
    return list(get_endpoint_catalog(**kwargs).endpoints)


def _catalog_for_call(
    *,
    session: Any,
    routing_group: str,
    legacy_base_url: str | None,
    legacy_model: str | None,
    legacy_api_key: str | None,
    legacy_timeout_seconds: int,
    local_base_url: str | None,
    local_model: str | None,
    local_timeout_seconds: int | None,
) -> EndpointCatalog:
    """Resolve a catalog with the same legacy values a caller currently sees."""
    return get_endpoint_catalog(
        session=session,
        routing_group=routing_group,
        legacy_base_url=legacy_base_url,
        legacy_model=legacy_model,
        legacy_api_key=legacy_api_key,
        legacy_timeout_seconds=legacy_timeout_seconds,
        local_base_url=local_base_url,
        local_model=local_model,
        local_timeout_seconds=local_timeout_seconds,
    )


def build_chat_url(base_url: str, chat_path: str = DEFAULT_CHAT_PATH) -> str:
    """Resolve a relative chat path without allowing host/path escapes."""
    raw_base = str(base_url or "").strip()
    raw_path = str(chat_path or DEFAULT_CHAT_PATH).strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in raw_base + raw_path):
        raise ValueError("LLM endpoint contains control characters")
    try:
        parsed_base = urlsplit(raw_base)
        if (
            parsed_base.scheme.lower() not in {"http", "https"}
            or not parsed_base.hostname
            or parsed_base.username is not None
            or parsed_base.password is not None
            or parsed_base.query
            or parsed_base.fragment
        ):
            raise ValueError("LLM base URL must be an absolute http(s) origin/path")
        # A path is intentionally the only accepted second component.  An
        # absolute URL or network-path reference could otherwise escape the
        # configured host when joined.
        parsed_path = urlsplit(raw_path)
        if parsed_path.scheme or parsed_path.netloc or parsed_path.query or parsed_path.fragment:
            raise ValueError("LLM chat_path must be a path, not a URL")
        if "\\" in raw_path or "//" in raw_path or any(part == ".." for part in parsed_path.path.split("/")):
            raise ValueError("LLM chat_path contains an unsafe path")
        path = parsed_path.path or DEFAULT_CHAT_PATH
        if not path.startswith("/"):
            path = "/" + path
        base_path = parsed_base.path.rstrip("/")
        path_parts = [part for part in path.split("/") if part]
        base_parts = [part for part in base_path.split("/") if part]
        if base_path and path != base_path and not path.startswith(base_path + "/"):
            # The table default is /v1/chat/completions while operators often
            # save a base ending in /v1.  Avoid producing /v1/v1/... in that
            # common representation; otherwise treat chat_path as relative
            # to the configured base path.
            if base_parts and path_parts and base_parts[-1] == path_parts[0]:
                path = base_path + "/" + "/".join(path_parts[1:])
            else:
                path = base_path + path
        authority = parsed_base.netloc
        return urlunsplit((parsed_base.scheme.lower(), authority, path or "/", "", ""))
    except ValueError:
        raise
    except (TypeError, AttributeError) as exc:
        raise ValueError("LLM endpoint URL is invalid") from exc


def _same_endpoint(left: Any, right: EndpointConfig) -> bool:
    """Compare effective endpoint identity without exposing credentials.

    Operators may represent the same llama.cpp server as either
    ``http://127.0.0.1:8930`` + ``/v1/chat/completions`` or
    ``http://127.0.0.1:8930/v1`` + the default path.  Resolve both through the
    same URL builder before deciding whether the launcher fallback duplicates
    a persisted row.
    """
    try:
        left_url = build_chat_url(
            str(_row_value(left, "base_url", "") or ""),
            str(_row_value(left, "chat_path", DEFAULT_CHAT_PATH) or DEFAULT_CHAT_PATH),
        )
        right_url = build_chat_url(right.base_url, right.chat_path)
    except (TypeError, ValueError):
        left_url = str(_row_value(left, "base_url", "") or "").rstrip("/")
        right_url = right.base_url.rstrip("/")
    left_model = str(_row_value(left, "model", "") or "").strip()
    return (left_url.rstrip("/").lower(), left_model) == (
        right_url.rstrip("/").lower(),
        right.model.strip(),
    )


def _content_from_response(data: Any) -> str:
    """Extract normal OpenAI-compatible content, returning empty on no text."""
    if not isinstance(data, Mapping):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    message = first.get("message")
    content = message.get("content") if isinstance(message, Mapping) else first.get("text")
    if isinstance(content, str):
        return content.strip()
    # Some OpenAI-compatible providers use a content-part list.
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, Mapping) and part.get("text")
        ]
        return "".join(parts).strip()
    return ""


def _http_status(response: Any) -> int | None:
    status = getattr(response, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _attempt_record(endpoint: EndpointConfig, *, status: str, **details: Any) -> dict[str, Any]:
    record = safe_endpoint_view(endpoint)
    record["status"] = status
    # Keep details to deliberately bounded, non-secret observability fields.
    for key in ("error_class", "http_status", "latency_ms"):
        if details.get(key) is not None:
            record[key] = details[key]
    return record


def _models_url(endpoint_config: EndpointConfig) -> str:
    """Derive the conventional models probe URL from the chat endpoint."""
    chat_url = build_chat_url(endpoint_config.base_url, endpoint_config.chat_path)
    parsed = urlsplit(chat_url)
    suffix = "/chat/completions"
    path = parsed.path[:-len(suffix)] + "/models" if parsed.path.endswith(suffix) else parsed.path.rstrip("/") + "/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def check_connection(
    *,
    session: Any = None,
    routing_group: str = DEFAULT_ROUTING_GROUP,
    http_client_factory: Callable[..., Any] | None = None,
    client_factory: Callable[..., Any] | None = None,
    legacy_base_url: str | None = None,
    legacy_model: str | None = None,
    legacy_api_key: str | None = None,
    legacy_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    local_base_url: str | None = None,
    local_model: str | None = None,
    local_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Probe each configured endpoint's conventional ``/models`` route.

    This is kept separate from :func:`call_chat` because a connection probe
    has no generation result.  It nevertheless shares endpoint selection,
    URL validation, failover ordering, and key-free attempt metadata.
    """
    if http_client_factory is not None and client_factory is not None:
        raise ValueError("provide only one HTTP client factory")
    factory = http_client_factory or client_factory or httpx.Client
    catalog = _catalog_for_call(
        session=session,
        routing_group=routing_group,
        legacy_base_url=legacy_base_url,
        legacy_model=legacy_model,
        legacy_api_key=legacy_api_key,
        legacy_timeout_seconds=legacy_timeout_seconds,
        local_base_url=local_base_url,
        local_model=local_model,
        local_timeout_seconds=local_timeout_seconds,
    )
    if not catalog.endpoints:
        return {
            "ok": False,
            "error": "RAG LLM endpoint pool is not configured or all configured endpoints are disabled",
            "attempts": [],
        }

    attempts: list[dict[str, Any]] = []
    for endpoint_config in catalog.endpoints:
        started = time.monotonic()
        try:
            probe_url = validate_llm_outbound_url(_models_url(endpoint_config))
        except Exception as exc:
            attempts.append(
                _attempt_record(
                    endpoint_config,
                    status="rejected",
                    error_class=exc.__class__.__name__,
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
            )
            continue

        headers = {"Content-Type": "application/json"}
        if endpoint_config.api_key:
            headers["Authorization"] = f"Bearer {endpoint_config.api_key}"
        try:
            with factory(timeout=endpoint_config.timeout_seconds) as client:
                # A connection probe is a read-only models discovery request.
                # Do not send a chat-completion POST here: llama.cpp and most
                # OpenAI-compatible servers expose ``GET /v1/models`` while
                # reserving POST for ``/chat/completions`` generation.
                response = client.get(probe_url, headers=headers)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                elif (_http_status(response) or 0) >= 400:
                    raise httpx.HTTPStatusError(
                        f"LLM endpoint returned {_http_status(response)}",
                        request=httpx.Request("GET", probe_url),
                        response=response,
                    )
                models = response.json()
            attempts.append(
                _attempt_record(
                    endpoint_config,
                    status="success",
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
            )
            return {
                "ok": True,
                "models": models,
                "attempts": attempts,
                "effective_endpoint": safe_endpoint_view(endpoint_config),
            }
        except Exception as exc:
            attempts.append(
                _attempt_record(
                    endpoint_config,
                    status="failed",
                    error_class=exc.__class__.__name__,
                    http_status=_http_status(getattr(exc, "response", None)),
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
            )
    return {
        "ok": False,
        "error": f"all {len(catalog.endpoints)} configured RAG LLM endpoint(s) failed",
        "attempts": attempts,
    }


def call_chat(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.1,
    routing_group: str = DEFAULT_ROUTING_GROUP,
    session: Any = None,
    http_client_factory: Callable[..., Any] | None = None,
    client_factory: Callable[..., Any] | None = None,
    legacy_base_url: str | None = None,
    legacy_model: str | None = None,
    legacy_api_key: str | None = None,
    legacy_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    local_base_url: str | None = None,
    local_model: str | None = None,
    local_timeout_seconds: int | None = None,
) -> LLMCallResult:
    """Call endpoints in priority order and fail over on unusable results."""
    if http_client_factory is not None and client_factory is not None:
        raise ValueError("provide only one HTTP client factory")
    factory = http_client_factory or client_factory or httpx.Client
    catalog = _catalog_for_call(
        session=session,
        routing_group=routing_group,
        legacy_base_url=legacy_base_url,
        legacy_model=legacy_model,
        legacy_api_key=legacy_api_key,
        legacy_timeout_seconds=legacy_timeout_seconds,
        local_base_url=local_base_url,
        local_model=local_model,
        local_timeout_seconds=local_timeout_seconds,
    )
    if not catalog.endpoints:
        raise LLMPoolError(
            "RAG LLM endpoint pool is not configured or all configured endpoints are disabled",
            catalog=catalog,
        )

    attempts: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    for endpoint_config in catalog.endpoints:
        started = time.monotonic()
        try:
            resolved_url = build_chat_url(endpoint_config.base_url, endpoint_config.chat_path)
            endpoint = validate_llm_outbound_url(resolved_url)
        except Exception as exc:
            attempts.append(
                _attempt_record(
                    endpoint_config,
                    status="rejected",
                    error_class=exc.__class__.__name__,
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
            )
            continue

        headers = {"Content-Type": "application/json"}
        if endpoint_config.api_key:
            headers["Authorization"] = f"Bearer {endpoint_config.api_key}"
        request_payload = dict(payload)
        request_payload["model"] = endpoint_config.model
        try:
            # Validate first, then construct the client.  This ordering is
            # intentional and is protected by the SSRF regression tests.
            with factory(timeout=endpoint_config.timeout_seconds) as client:
                response = client.post(endpoint, headers=headers, json=request_payload)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                elif (_http_status(response) or 0) >= 400:
                    raise httpx.HTTPStatusError(
                        f"LLM endpoint returned {_http_status(response)}",
                        request=httpx.Request("POST", endpoint),
                        response=response,
                    )
                data = response.json()
                content = _content_from_response(data)
                elapsed = round((time.monotonic() - started) * 1000)
                if not content:
                    attempts.append(
                        _attempt_record(
                            endpoint_config,
                            status="empty",
                            latency_ms=elapsed,
                        )
                    )
                    continue
                attempts.append(
                    _attempt_record(
                        endpoint_config,
                        status="success",
                        latency_ms=elapsed,
                    )
                )
                return LLMCallResult(
                    text=content,
                    attempts=attempts,
                    effective_endpoint=safe_endpoint_view(endpoint_config),
                )
        except Exception as exc:
            status_code = _http_status(getattr(exc, "response", None))
            attempts.append(
                _attempt_record(
                    endpoint_config,
                    status="failed",
                    error_class=exc.__class__.__name__,
                    http_status=status_code,
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
            )
            continue

    raise LLMPoolError(
        f"all {len(catalog.endpoints)} configured RAG LLM endpoint(s) failed",
        attempts=attempts,
        catalog=catalog,
    )


def call_llm(*args: Any, **kwargs: Any) -> LLMCallResult:
    """Alias for adapters that use the service-level name ``call_llm``."""
    return call_chat(*args, **kwargs)


def llm_available(
    *,
    session: Any = None,
    legacy_base_url: str | None = None,
    legacy_model: str | None = None,
    legacy_api_key: str | None = None,
    local_base_url: str | None = None,
    local_model: str | None = None,
    local_timeout_seconds: int | None = None,
) -> bool:
    """Return whether at least one endpoint is configured, without probing it."""
    return get_endpoint_catalog(
        session=session,
        legacy_base_url=legacy_base_url,
        legacy_model=legacy_model,
        legacy_api_key=legacy_api_key,
        local_base_url=local_base_url,
        local_model=local_model,
        local_timeout_seconds=local_timeout_seconds,
    ).configured


__all__ = [
    "DEFAULT_CHAT_PATH",
    "EndpointCatalog",
    "EndpointConfig",
    "LLMCallResult",
    "LLMEndpoint",
    "LLMPoolError",
    "build_chat_url",
    "call_chat",
    "call_llm",
    "check_connection",
    "get_endpoint_catalog",
    "llm_available",
    "load_endpoints",
    "safe_endpoint_view",
]
