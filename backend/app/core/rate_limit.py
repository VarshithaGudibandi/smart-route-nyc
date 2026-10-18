"""Fixed-window rate limiting shared across instances through Redis.

Fixed windows can admit up to twice the limit across a window boundary; that
burst is acceptable here and keeps each check to one Redis round trip. If
Redis is unavailable, limits fall back to a per-process counter: weaker, but
still bounded.
"""

import hashlib
import hmac
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.cache.redis import ResilientRedis


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int


class RateLimiter:
    def __init__(
        self,
        redis: ResilientRedis,
        secret: bytes,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._secret = secret
        self._clock = clock
        self._local: dict[str, tuple[int, float]] = {}

    async def hit(self, scope: str, client_id: str, limit: int, window_s: int) -> RateLimitDecision:
        now = self._clock()
        window = int(now // window_s)
        # Client addresses are keyed by HMAC so Redis never holds raw IPs.
        client = hmac.new(self._secret, client_id.encode(), hashlib.sha256).hexdigest()[:16]
        key = f"srn:v1:rl:{scope}:{client}:{window}"

        count = await self._redis.increment_window(key, window_s)
        if count is None:
            count = self._local_increment(key, now, window_s)

        retry_after = math.ceil((window + 1) * window_s - now)
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after_s=max(1, retry_after),
        )

    def _local_increment(self, key: str, now: float, window_s: int) -> int:
        if len(self._local) > 10_000:
            self._local = {k: v for k, v in self._local.items() if v[1] > now}
        count, expires = self._local.get(key, (0, now + window_s))
        self._local[key] = (count + 1, expires)
        return count + 1
