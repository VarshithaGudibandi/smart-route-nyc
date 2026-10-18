"""Test doubles that keep the real client code in the loop.

``fake_otp`` returns a real ``OtpClient`` whose HTTP transport is replaced,
so query building, retries and the circuit breaker are exercised for real.
``InMemoryRedis`` stands in for the redis-py client underneath
``ResilientRedis``.
"""

import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.cache.redis import ResilientRedis
from app.core.resilience import CircuitBreaker
from app.integrations.otp.client import OtpClient
from app.schemas.routes import (
    AccessibilityRequirements,
    Location,
    OptimizationPreferences,
    RouteSearchRequest,
)

FIXTURES = Path(__file__).parent / "fixtures"
JOURNAL_SQUARE = Location(lat=40.73301, lon=-74.06289)
COLUMBIA = Location(lat=40.80755, lon=-73.96449)


def plan_fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "otp_plan_journal_square_to_columbia.json").read_text()
    )
    return data


def make_request(
    depart_at: datetime | None = None,
    accessibility: AccessibilityRequirements | None = None,
    optimization: OptimizationPreferences | None = None,
) -> RouteSearchRequest:
    """Build a request without the "not in the past" check, so tests can use
    the fixture's fixed timestamps on any date."""
    return RouteSearchRequest.model_construct(
        origin=JOURNAL_SQUARE,
        destination=COLUMBIA,
        depart_at=depart_at,
        accessibility=accessibility or AccessibilityRequirements(),
        optimization=optimization or OptimizationPreferences(),
    )


class FakeOtpServer:
    """Answers GraphQL plan requests with the recorded fixture."""

    def __init__(self, delay_s: float = 0.0, failures: int = 0, status: int = 503) -> None:
        self.calls = 0
        self.requests: list[dict[str, Any]] = []
        self.delay_s = delay_s
        self.failures = failures
        self.status = status
        self.payload = plan_fixture()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.requests.append(json.loads(request.content))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.failures > 0:
            self.failures -= 1
            return httpx.Response(self.status)
        return httpx.Response(200, json=self.payload)


def fake_otp(
    handler: Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]],
    retries: int = 0,
    failure_threshold: int = 5,
) -> OtpClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://otp")
    return OtpClient(http, CircuitBreaker("otp", failure_threshold, 15), retries=retries)


class InMemoryRedis:
    """The subset of redis.asyncio.Redis used by ResilientRedis (TTLs ignored)."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def set(
        self,
        key: str,
        value: bytes | str,
        ex: int | None = None,
        nx: bool = False,
        px: int | None = None,
    ) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value.encode() if isinstance(value, str) else value
        return True

    async def eval(self, script: str, numkeys: int, key: str, value: str) -> int:
        if self.data.get(key) == value.encode():
            del self.data[key]
            return 1
        return 0

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def in_memory_redis() -> ResilientRedis:
    return ResilientRedis(InMemoryRedis(), CircuitBreaker("redis", 3, 10))  # type: ignore[arg-type]
