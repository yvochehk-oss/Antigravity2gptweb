"""Safe request-validation responses for the RAG HTTP boundary.

Pydantic includes the rejected ``input`` value in its normal validation
errors.  That is useful for ordinary fields, but it would echo a submitted
LLM API key back to the browser and potentially into a reverse proxy log.
Keep the response useful while removing all raw input and context values; the
API-key field gets a deliberately generic message as an additional guard.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _is_api_key_location(location: Iterable[Any]) -> bool:
    return any(str(part).lower() == "api_key" for part in location)


def _safe_error(error: dict[str, Any]) -> dict[str, Any]:
    """Keep only non-sensitive validation fields.

    ``input`` and ``ctx`` are intentionally omitted.  ``ctx`` can contain a
    validator exception whose string representation includes the submitted
    value even when ``input`` itself is not present.
    """
    location = tuple(error.get("loc") or ())
    return {
        "type": str(error.get("type") or "value_error"),
        "loc": list(location),
        "msg": (
            "api_key 参数无效"
            if _is_api_key_location(location)
            else str(error.get("msg") or "请求参数无效")
        ),
    }


async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return a credential-free 422 response for all request bodies."""
    del request
    return JSONResponse(
        status_code=422,
        content={"detail": [_safe_error(error) for error in exc.errors()]},
    )


__all__ = ["request_validation_exception_handler"]
