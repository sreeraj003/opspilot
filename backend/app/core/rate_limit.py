"""Rate limiting for authentication endpoints.

``RateLimiter`` is the interface the API depends on. ``InMemoryRateLimiter``
is a sliding-window implementation held in process memory: correct for the
supported single-process deployment (one uvicorn worker, as in the Docker
image). Running several workers or replicas multiplies the effective limits;
a shared store (for example Redis) can implement the same interface later
without touching the endpoints.
"""

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        """Record one attempt for ``key`` and say whether it is within ``limit`` per window."""
        ...

    def reset(self, key: str) -> None: ...


class InMemoryRateLimiter:
    """Sliding-window log per key, bounded in memory.

    Not thread-safe by design: it is used from the event loop only (async
    endpoints), so no two calls interleave.
    """

    def __init__(self, *, max_keys: int = 50_000, clock: Callable[[], float] = time.monotonic) -> None:
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._max_keys = max_keys
        self._clock = clock

    def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        now = self._clock()
        window_start = now - window_seconds
        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
            self._evict_if_full()
        else:
            self._hits.move_to_end(key)
        while hits and hits[0] <= window_start:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = int(hits[0] + window_seconds - now) + 1
            return RateLimitResult(allowed=False, retry_after_seconds=retry_after)
        hits.append(now)
        return RateLimitResult(allowed=True)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def _evict_if_full(self) -> None:
        # Least recently used keys go first; an attacker rotating keys can only
        # evict their own stale entries once the map is full.
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)
