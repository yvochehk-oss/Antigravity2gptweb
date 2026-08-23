"""Simple in-memory rate limiter middleware.

Limits requests per IP per minute. Uses a sliding window approach.
For production, consider Redis-backed rate limiting.
"""
import time
import threading
from collections import defaultdict, deque
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.requests import Request
from .config import RATE_LIMIT_PER_MINUTE
from .logging_config import get_logger

logger = get_logger(__name__)

# Thread-safe storage: IP -> deque of timestamps
_request_log: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory rate limit middleware.

    Limits each IP to RATE_LIMIT_PER_MINUTE requests per minute.
    Returns HTTP 429 when limit exceeded.
    """

    def __init__(self, app, limit: int = RATE_LIMIT_PER_MINUTE):
        super().__init__(app)
        self.limit = limit
        self.window_seconds = 60

    async def dispatch(self, request: Request, call_next):
        # Skip rate limit for health checks
        if request.url.path in ("/api/v1/health", "/"):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        now = time.time()

        with _lock:
            timestamps = _request_log[client_ip]

            # Remove timestamps outside the window
            cutoff = now - self.window_seconds
            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()

            # Check limit
            if len(timestamps) >= self.limit:
                logger.warning(f"Rate limit exceeded for {client_ip}")
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limit_exceeded",
                        "message": f"Too many requests. Limit: {self.limit}/{self.window_seconds}s",
                        "retry_after": self.window_seconds
                    }
                )

            # Record this request
            timestamps.append(now)

        return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request.

        Checks X-Forwarded-For header first (for proxied requests),
        falls back to client.host.

        Args:
            request: Starlette request

        Returns:
            Client IP address string
        """
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


def clear_rate_limit_cache():
    """Clear all rate limit tracking data.

    Useful for testing or after configuration changes.
    """
    with _lock:
        _request_log.clear()
    logger.info("Rate limit cache cleared")