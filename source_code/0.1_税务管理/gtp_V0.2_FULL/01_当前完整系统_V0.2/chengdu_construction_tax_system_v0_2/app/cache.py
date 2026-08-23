"""V0.2: 简易缓存。"""
from __future__ import annotations

import threading
import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """线程安全的 TTL 缓存。"""

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, T]] = {}

    def get(self, key: str, loader: Callable[[], T]) -> T:
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached and now - cached[0] < self._ttl:
                return cached[1]
        # 锁外加载，避免 loader 阻塞其他 key
        value = loader()
        with self._lock:
            self._store[key] = (now, value)
        return value

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)


# 模块级缓存实例
endpoint_cache: TTLCache[list[int]] = TTLCache(ttl_seconds=30.0)
tax_ledger_cache: TTLCache[str] = TTLCache(ttl_seconds=300.0)