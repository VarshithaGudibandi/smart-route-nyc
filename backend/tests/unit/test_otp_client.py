from datetime import datetime

import httpx
import pytest

from app.core.errors import RoutingTimeoutError, RoutingUnavailableError
from app.integrations.otp.client import OtpQueryError, PlanQuery
from tests.fakes import FakeOtpServer, fake_otp

QUERY = PlanQuery(
    origin=(40.73301, -74.06289),
    destination=(40.80755, -73.96449),
    earliest_departure=datetime.fromisoformat("2026-10-19T08:30:00-04:00"),
    itineraries=3,
    wheelchair=True,
    walk_reluctance=4.0,
)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.otp.client.random.uniform", lambda a, b: 0.0)


async def test_sends_preferences_and_parses_itineraries() -> None:
    server = FakeOtpServer()
    result = await fake_otp(server.handler).plan(QUERY)

    assert len(result.itineraries) == 2
    variables = server.requests[0]["variables"]
    assert variables["preferences"]["accessibility"] == {"wheelchair": {"enabled": True}}
    assert variables["preferences"]["street"] == {"walk": {"reluctance": 4.0}}
    assert variables["dateTime"] == {"earliestDeparture": "2026-10-19T08:30:00-04:00"}


async def test_retries_once_on_transient_failure() -> None:
    server = FakeOtpServer(failures=1)
    result = await fake_otp(server.handler, retries=1).plan(QUERY)
    assert server.calls == 2
    assert result.itineraries


async def test_gives_up_after_retries() -> None:
    server = FakeOtpServer(failures=5)
    with pytest.raises(RoutingUnavailableError):
        await fake_otp(server.handler, retries=1).plan(QUERY)
    assert server.calls == 2


async def test_timeout_maps_to_routing_timeout() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(RoutingTimeoutError):
        await fake_otp(slow).plan(QUERY)


async def test_graphql_errors_are_not_retried() -> None:
    calls = 0

    async def invalid(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"errors": [{"message": "Field 'x' is undefined"}]})

    client = fake_otp(invalid, retries=2)
    with pytest.raises(OtpQueryError):
        await client.plan(QUERY)
    assert calls == 1
    assert client.breaker.state == "closed"


async def test_open_breaker_fails_fast_without_calling_otp() -> None:
    server = FakeOtpServer(failures=100)
    client = fake_otp(server.handler, failure_threshold=2)
    for _ in range(2):
        with pytest.raises(RoutingUnavailableError):
            await client.plan(QUERY)
    calls = server.calls

    with pytest.raises(RoutingUnavailableError):
        await client.plan(QUERY)
    assert server.calls == calls
