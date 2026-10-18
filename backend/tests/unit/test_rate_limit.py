from app.cache.redis import ResilientRedis
from app.core.rate_limit import RateLimiter
from app.core.resilience import CircuitBreaker
from tests.unit.test_redis_resilience import FailingRedis


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def limiter(clock: Clock) -> RateLimiter:
    # Redis always fails, so this exercises the per-process fallback.
    redis = ResilientRedis(FailingRedis(), CircuitBreaker("redis", 1, 60))  # type: ignore[arg-type]
    return RateLimiter(redis, b"secret", clock=clock)


async def test_allows_up_to_limit_then_rejects_with_retry_after() -> None:
    clock = Clock(999_980.0)  # 999_980 % 60 == 20: 20 s into a 60 s window
    rl = limiter(clock)
    decisions = [await rl.hit("search", "1.2.3.4", limit=3, window_s=60) for _ in range(4)]

    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.remaining for d in decisions] == [2, 1, 0, 0]
    assert decisions[-1].retry_after_s == 40


async def test_new_window_resets_the_count() -> None:
    clock = Clock(1_000_020.0)
    rl = limiter(clock)
    for _ in range(3):
        await rl.hit("search", "1.2.3.4", limit=3, window_s=60)
    clock.now += 60
    assert (await rl.hit("search", "1.2.3.4", limit=3, window_s=60)).allowed


async def test_clients_and_scopes_are_independent() -> None:
    rl = limiter(Clock(1_000_020.0))
    await rl.hit("search", "1.2.3.4", limit=1, window_s=60)
    assert (await rl.hit("search", "5.6.7.8", limit=1, window_s=60)).allowed
    assert (await rl.hit("explain", "1.2.3.4", limit=1, window_s=60)).allowed
