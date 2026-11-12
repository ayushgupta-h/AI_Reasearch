"""
app/infrastructure/local_cache.py
=================================
Single-process, thread-safe cache with optional TTL.

This is a development/test adapter for :class:`app.infrastructure.ports.
CacheService`. It stores entries in memory, so values are lost on restart and
are not shared between workers — intentional, and documented in the class.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional, Tuple


class LocalCacheService:
    """In-memory cache; not a distributed cache."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 10_000,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._clock = clock
        self._max_entries = max_entries
        self._lock = threading.RLock()
        # key -> (value, expires_at or None)
        self._entries: Dict[str, Tuple[str, Optional[float]]] = {}

    # ── CacheService ──────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        self._validate_key(key)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at is not None and self._clock() >= expires_at:
                self._entries.pop(key, None)
                return None
            return value

    def set(self, key: str, value: str, ttl_seconds: Optional[float] = None) -> None:
        self._validate_key(key)
        if not isinstance(value, str):
            raise TypeError("Cache values must be strings")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive when provided")

        expires_at = None if ttl_seconds is None else self._clock() + float(ttl_seconds)
        with self._lock:
            self._make_room()
            self._entries[key] = (value, expires_at)

    def delete(self, key: str) -> bool:
        self._validate_key(key)
        with self._lock:
            return self._entries.pop(key, None) is not None

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    # ── Extras ────────────────────────────────────────────────────────────

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired()
            return len(self._entries)

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("Cache keys must be non-empty strings")

    def _purge_expired(self) -> None:
        now = self._clock()
        for key, (_, expires_at) in list(self._entries.items()):
            if expires_at is not None and now >= expires_at:
                self._entries.pop(key, None)

    def _make_room(self) -> None:
        if len(self._entries) < self._max_entries:
            return
        self._purge_expired()
        while len(self._entries) >= self._max_entries:
            # Oldest insertion first; callers needing LRU should use a real cache.
            oldest = next(iter(self._entries))
            self._entries.pop(oldest, None)


__all__ = ["LocalCacheService"]
