from app.security.rate_limit import InMemoryRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_blocks_after_limit_and_recovers_after_window() -> None:
    clock = FakeClock()
    limiter = InMemoryRateLimiter(clock=clock)
    for _ in range(3):
        assert limiter.check("k", 3, 60).allowed
        limiter.hit("k", 60)
    status = limiter.check("k", 3, 60)
    assert not status.allowed and 1 <= status.retry_after_seconds <= 61

    clock.now += 61
    assert limiter.check("k", 3, 60).allowed


def test_reset_and_key_isolation() -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock())
    limiter.hit("a", 60)
    assert not limiter.check("a", 1, 60).allowed
    assert limiter.check("b", 1, 60).allowed
    limiter.reset("a")
    assert limiter.check("a", 1, 60).allowed


def test_memory_is_bounded() -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock(), max_keys=10)
    for i in range(50):
        limiter.hit(f"k{i}", 60)
    assert len(limiter._events) <= 10
