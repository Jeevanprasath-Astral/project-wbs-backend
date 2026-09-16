"""
cache_service.py
────────────────
Lightweight in-memory TTL cache for expensive computed results.

Usage:
    from app.services.cache_service import cache

    # Store a value (default TTL = 300 s / 5 min)
    cache.set("my_key", data)

    # Retrieve — returns None if missing or expired
    data = cache.get("my_key")

    # Invalidate a single key (call after a write that changes the data)
    cache.delete("my_key")

    # Invalidate every key that starts with a prefix
    cache.delete_prefix("global:")
"""

import time
import threading
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 300  # 5 minutes


class TTLCache:
    """Thread-safe dict-backed TTL cache."""

    def __init__(self):
        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ── Core operations ────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry["expires_at"]:
                del self._store[key]
                return None
            return entry["data"]

    def set(self, key: str, data: Any, ttl: int = _DEFAULT_TTL) -> None:
        with self._lock:
            self._store[key] = {
                "data":       data,
                "expires_at": time.monotonic() + ttl,
                "set_at":     time.time(),
            }
        logger.debug("Cache SET  key=%s  ttl=%ds", key, ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)
        logger.debug("Cache DEL  key=%s", key)

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
        if keys:
            logger.debug("Cache DEL_PREFIX  prefix=%s  removed=%d", prefix, len(keys))

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
        logger.debug("Cache CLEAR")

    # ── Introspection (for /health or admin endpoints) ──────────────────────

    def keys(self) -> list[str]:
        with self._lock:
            now = time.monotonic()
            return [k for k, v in self._store.items() if v["expires_at"] > now]

    def size(self) -> int:
        return len(self.keys())


# Module-level singleton — import this everywhere
cache = TTLCache()
