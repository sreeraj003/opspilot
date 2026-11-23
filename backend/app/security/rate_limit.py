"""Rate limiting foundation.

``RateLimiter`` is the interface services depend on. ``InMemoryRateLimiter`` is a
sliding-window implementation suitable for a single process; a shared backend
(for example Redis) can implement the same interface for multi-instance
deployments without changing callers.
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RateLimitStatus:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitStatus:
        """Report whether another event for ``key`` is allowed, without recording one."""

    def hit(self, key: str, window_seconds: int) -> None:
        """Record one event for ``key``."""

    def reset(self, key: str) -> None:
        """Forget all events for ``key``."""


class InMemoryRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = 100_000) -> None:
        self._clock = clock
        self._max_keys = max_keys
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, window_seconds: int, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            return deque()
        while events and events[0] <= now - window_seconds:
            events.popleft()
        if not events:
            del self._events[key]
        return events

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitStatus:
        with self._lock:
            now = self._clock()
            events = self._prune(key, window_seconds, now)
            if len(events) < limit:
                return RateLimitStatus(allowed=True)
            retry_after = int(events[0] + window_seconds - now) + 1
            return RateLimitStatus(allowed=False, retry_after_seconds=retry_after)

    def hit(self, key: str, window_seconds: int) -> None:
        with self._lock:
            now = self._clock()
            events = self._prune(key, window_seconds, now)
            if not events:
                if len(self._events) >= self._max_keys:
                    # Bound memory under key-spraying: drop the oldest tracked key.
                    self._events.pop(next(iter(self._events)))
                self._events[key] = events
            events.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
