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

# Compare-and-delete in one round trip, so a lock that expired and was
# re-acquired by another instance is never released by the old holder.
_DELETE_IF_EQUALS = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""


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

    async def set_if_absent(self, key: str, value: str, ttl_ms: int) -> bool | None:
        """Atomically set ``key`` if it does not exist.

        Returns True if set, False if the key already existed, and None if
        Redis is unavailable (callers must decide how to proceed without it).
        """
        try:
            self.breaker.before_call()
        except CircuitOpenError:
            return None
        try:
            created = await self._client.set(key, value, nx=True, px=ttl_ms)
        except (RedisError, OSError) as exc:
            self._failed("set_if_absent", exc)
            return None
        self.breaker.record_success()
        return bool(created)

    async def delete_if_equals(self, key: str, value: str) -> None:
        """Delete ``key`` only if it still holds ``value`` (safe lock release)."""
        try:
            self.breaker.before_call()
        except CircuitOpenError:
            return
        try:
            await self._client.eval(_DELETE_IF_EQUALS, 1, key, value)
        except (RedisError, OSError) as exc:
            self._failed("delete_if_equals", exc)
            return
        self.breaker.record_success()

    async def increment_window(self, key: str, ttl_s: int) -> int | None:
        """Increment a counter, setting its expiry on first use. None if unavailable."""
        try:
            self.breaker.before_call()
        except CircuitOpenError:
            return None
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, ttl_s, nx=True)
                count, _ = await pipe.execute()
        except (RedisError, OSError) as exc:
            self._failed("increment_window", exc)
            return None
        self.breaker.record_success()
        return int(count)

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
