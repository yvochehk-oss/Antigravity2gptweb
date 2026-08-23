"""Backward-compatible import for the request-id middleware.

The implementation lives beside the rate limiter in :mod:`app.middleware`
so the middleware stack has one authoritative tracing component.  Existing
imports from this module remain valid for integrations and older tests.
"""

from .middleware import RequestIdMiddleware

__all__ = ["RequestIdMiddleware"]
