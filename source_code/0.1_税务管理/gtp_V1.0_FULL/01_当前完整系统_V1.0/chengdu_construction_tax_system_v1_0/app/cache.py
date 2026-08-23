"""V0.2: 简易缓存。

The ``TTLCache`` is a thread-safe TTL+LRU cache.  Entries are evicted
when their TTL expires or when the cache exceeds ``max_capacity``; the
latter uses a strict LRU policy so the most recently used keys remain
in memory while cold keys are released.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """线程安全的 TTL + LRU 缓存。"""

    def __init__(
        self,
        ttl_seconds: float = 30.0,
        max_capacity: int | None = None,
    ) -> None:
        if max_capacity is not None and max_capacity <= 0:
            raise ValueError("max_capacity must be > 0 when provided")
        self._ttl = ttl_seconds
        self._max_capacity = max_capacity
        self._lock = threading.RLock()
        # ``OrderedDict`` doubles as the LRU index and the storage backend.
        # The value tuple is (timestamp, payload).
        self._store: OrderedDict[str, tuple[float, T]] = OrderedDict()

    @property
    def max_capacity(self) -> int | None:
        return self._max_capacity

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def _purge_expired(self, now: float) -> None:
        """Erase expired entries.  Caller must hold the lock."""
        if self._ttl <= 0:
            return
        expired_keys = [
            key for key, (ts, _) in self._store.items() if now - ts >= self._ttl
        ]
        for key in expired_keys:
            stored = self._store.pop(key, None)
            # ``stored_ts`` is kept for clarity when the cache is inspected
            # during debugging; the value is intentionally ignored here.
            _ = stored

    def _evict_to_capacity(self) -> None:
        """Drop oldest entries until the cache is within capacity.  Caller must hold the lock."""
        if self._max_capacity is None:
            return
        while len(self._store) > self._max_capacity:
            self._store.popitem(last=False)

    def set(self, key: str, value: T) -> None:
        """Store ``value`` and apply the configured capacity limit.

        The timestamp is recorded at the time the value is stored rather than
        when a potentially slow loader started.  This prevents a slow remote
        call from returning an entry that is already expired.
        """
        with self._lock:
            self._purge_expired(time.monotonic())
            self._store[key] = (time.monotonic(), value)
            self._store.move_to_end(key)
            self._evict_to_capacity()

    def get_with_status(
        self,
        key: str,
        loader: Callable[[], T],
    ) -> tuple[T, str]:
        """Return ``(value, cache_status)`` while avoiding failure caching.

        ``cache_status`` is one of ``hit``, ``miss`` or ``expired``.  A
        loader exception is propagated and no value is stored, so callers can
        surface dependency failure instead of turning it into a cached
        success/degraded response.
        """
        now = time.monotonic()
        status = "miss"
        with self._lock:
            cached = self._store.get(key)
            if cached is not None:
                if self._ttl > 0 and now - cached[0] < self._ttl:
                    self._store.move_to_end(key)
                    return cached[1], "hit"
                # Remember that this exact key existed but expired.  Other
                # expired keys are purged below without changing this signal.
                status = "expired"
                self._store.pop(key, None)
            self._purge_expired(now)

        # Loader runs outside the lock so one slow Facts request does not
        # block unrelated cache keys.  Exceptions intentionally bypass set().
        value = loader()
        self.set(key, value)
        return value, status

    def get(self, key: str, loader: Callable[[], T]) -> T:
        value, _ = self.get_with_status(key, loader)
        return value

    def invalidate_where(self, predicate: Callable[[str], bool]) -> int:
        """Invalidate keys matching ``predicate`` and return removal count."""
        with self._lock:
            keys = [key for key in self._store if predicate(key)]
            for key in keys:
                self._store.pop(key, None)
            return len(keys)

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)


# 模块级缓存实例
endpoint_cache: TTLCache[list[int]] = TTLCache(ttl_seconds=30.0, max_capacity=1024)
tax_ledger_cache: TTLCache[str] = TTLCache(ttl_seconds=300.0, max_capacity=128)
