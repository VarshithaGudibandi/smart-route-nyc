"""Redis access that degrades instead of failing.

Redis is an optimisation and coordination layer, never the source of truth.
Every operation has a short timeout and goes through a circuit breaker; when
Redis is slow or down, reads behave as cache misses and writes are skipped,
so callers fall back to computing results directly.
"""

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.resilience import CircuitBreaker, CircuitOpenError

logger = structlog.get_logger(__name__)


class ResilientRedis:
    def __init__(self, client: Redis, breaker: CircuitBreaker) -> None:
        self._client = client
        self.breaker = breaker

    @classmethod
    def from_url(
        cls, url: str, timeout_ms: int, failure_threshold: int, reset_timeout_s: float
    ) -> "ResilientRedis":
        timeout_s = timeout_ms / 1000
        client = Redis.from_url(
            url,
            socket_timeout=timeout_s,
            socket_connect_timeout=timeout_s,
            # Retries are owned by the breaker, not the client.
            retry_on_timeout=False,
        )
        return cls(client, CircuitBreaker("redis", failure_threshold, reset_timeout_s))

    async def ping(self) -> bool:
        """Check connectivity directly, bypassing the breaker (used by health checks)."""
        try:
            return bool(await self._client.ping())
        except (RedisError, OSError):
            return False

    async def get(self, key: str) -> bytes | None:
        try:
            self.breaker.before_call()
        except CircuitOpenError:
            return None
        try:
            value = await self._client.get(key)
        except (RedisError, OSError) as exc:
            self._failed("get", exc)
            return None
        self.breaker.record_success()
        # The client is created without decode_responses, so values are bytes.
        return value.encode() if isinstance(value, str) else value

    async def set(self, key: str, value: bytes, ttl_s: int) -> bool:
        try:
            self.breaker.before_call()
        except CircuitOpenError:
            return False
        try:
            await self._client.set(key, value, ex=ttl_s)
        except (RedisError, OSError) as exc:
            self._failed("set", exc)
            return False
        self.breaker.record_success()
        return True

    async def close(self) -> None:
        await self._client.aclose()

    def _failed(self, operation: str, exc: Exception) -> None:
        self.breaker.record_failure()
        logger.warning(
            "redis_operation_failed",
            operation=operation,
            error_type=type(exc).__name__,
            breaker_state=self.breaker.state,
        )
