from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache.redis import ResilientRedis
from app.core.resilience import BreakerState, CircuitBreaker


class FailingRedis:
    """Stand-in for redis.asyncio.Redis whose calls always fail."""

    def __init__(self) -> None:
        self.calls = 0

    async def get(self, key: str) -> bytes | None:
        self.calls += 1
        raise RedisConnectionError("connection refused")

    async def set(self, key: str, value: bytes | str, **options: Any) -> None:
        self.calls += 1
        raise RedisConnectionError("connection refused")

    def pipeline(self, transaction: bool = True) -> Any:
        self.calls += 1
        raise RedisConnectionError("connection refused")


def make(client: Any) -> ResilientRedis:
    return ResilientRedis(client, CircuitBreaker("redis", failure_threshold=2, reset_timeout_s=60))


async def test_failures_behave_as_misses_and_skipped_writes() -> None:
    redis = make(FailingRedis())
    assert await redis.get("k") is None
    assert await redis.set("k", b"v", ttl_s=10) is False


async def test_open_breaker_stops_calling_redis() -> None:
    client = FailingRedis()
    redis = make(client)
    await redis.get("k")
    await redis.get("k")
    assert redis.breaker.state is BreakerState.OPEN
    calls_when_opened = client.calls

    assert await redis.get("k") is None
    assert await redis.set("k", b"v", ttl_s=10) is False
    assert client.calls == calls_when_opened
