import asyncio
import dataclasses
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.cache.redis import ResilientRedis
from app.core.config import Settings
from app.core.graph_version import GraphVersion
from app.main import create_app
from app.services.route_search import CacheStatus, RouteSearchService
from tests.fakes import FakeOtpServer, fake_otp, make_request

DEPART = datetime.fromisoformat("2026-10-19T08:30:00-04:00")


def settings(
    redis_url: str, database_url: str = "postgresql+asyncpg://u:p@127.0.0.1:1/x"
) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        redis_url=redis_url,
        database_url=database_url,
        rate_limit_route_search_per_minute=3,
        redis_timeout_ms=500,
        # A fresh secret per test gives each test its own cache and
        # rate-limit keys in the shared Redis container.
        key_hashing_secret=uuid.uuid4().hex,
    )


def redis_client(url: str) -> ResilientRedis:
    return ResilientRedis.from_url(url, timeout_ms=500, failure_threshold=3, reset_timeout_s=10)


async def test_instances_share_one_computation_through_redis(redis_url: str) -> None:
    """Two backend instances (separate processes in production) searching the
    same trip at the same moment make one OTP call between them."""
    server = FakeOtpServer(delay_s=0.3)
    config = settings(redis_url)
    graph = GraphVersion(None)
    # A distinct departure keeps this test independent of other tests' cache entries.
    depart = DEPART + timedelta(days=1, minutes=17)
    instance_a = RouteSearchService(
        fake_otp(server.handler), redis_client(redis_url), graph, config
    )
    instance_b = RouteSearchService(
        fake_otp(server.handler), redis_client(redis_url), graph, config
    )

    outcomes = await asyncio.gather(
        instance_a.search(make_request(depart)), instance_b.search(make_request(depart))
    )
    assert server.calls == 1
    assert sorted(o.cache for o in outcomes) == [CacheStatus.MISS, CacheStatus.SHARED]
    assert outcomes[0].response.routes == outcomes[1].response.routes


@pytest.fixture
def server() -> FakeOtpServer:
    return FakeOtpServer()


@pytest.fixture
def client(redis_url: str, server: FakeOtpServer) -> Iterator[TestClient]:
    app = create_app(settings(redis_url))
    with TestClient(app) as client:
        res = app.state.resources
        otp = fake_otp(server.handler)
        route_search = RouteSearchService(otp, res.redis, GraphVersion(None), res.settings)
        app.state.resources = dataclasses.replace(res, otp=otp, route_search=route_search)
        yield client


def search_body(**overrides: Any) -> dict[str, Any]:
    depart = (datetime.now(UTC) + timedelta(days=2)).replace(minute=12, second=0, microsecond=0)
    body: dict[str, Any] = {
        "origin": {"lat": 40.73301, "lon": -74.06289},
        "destination": {"lat": 40.80755, "lon": -73.96449},
        "depart_at": depart.isoformat(),
    }
    return body | overrides


def test_search_endpoint_caches_and_reports_status(
    client: TestClient, server: FakeOtpServer
) -> None:
    body = search_body(accessibility={"max_walk_meters": 4000})
    first = client.post("/api/v1/routes/search", json=body)
    second = client.post("/api/v1/routes/search", json=body)

    assert first.status_code == 200
    assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("MISS", "HIT")
    assert server.calls == 1
    payload = first.json()
    assert payload["graph_version"] == "unversioned"
    assert all(route["accessibility_status"] == "unknown" for route in payload["routes"])


def test_rate_limit_returns_429_with_retry_after(client: TestClient) -> None:
    statuses = [
        client.post("/api/v1/routes/search", json=search_body()).status_code for _ in range(4)
    ]
    # The window may roll over mid-test, which only ever allows more requests.
    if statuses == [200, 200, 200, 200]:
        pytest.skip("rate-limit window rolled over during the test")
    assert statuses[:3] == [200, 200, 200]
    limited = client.post("/api/v1/routes/search", json=search_body())
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60


def test_routing_outage_returns_503_without_fabricated_routes(
    client: TestClient, server: FakeOtpServer
) -> None:
    server.failures = 10
    response = client.post(
        "/api/v1/routes/search", json=search_body(optimization={"primary": "LEAST_WALKING"})
    )
    assert response.status_code == 503
    assert response.json()["code"] == "routing_unavailable"
    assert "routes" not in response.json()


def test_invalid_request_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/routes/search", json=search_body(destination={"lat": 42.65, "lon": -73.75})
    )
    assert response.status_code == 422
    assert response.json()["details"][0]["loc"][:2] == ["body", "destination"]
