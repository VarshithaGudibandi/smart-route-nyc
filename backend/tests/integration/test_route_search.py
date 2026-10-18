import asyncio
import dataclasses
import json
import time
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.cache.redis import ResilientRedis
from app.core.config import Settings
from app.core.graph_version import GraphVersion
from app.main import create_app
from app.schemas.routes import Route, RouteSearchRequest
from app.schemas.suggestions import (
    InvestigationResult,
    RejectedProposal,
    TransferProposal,
    VerifiedSuggestion,
)
from app.services.cache_keys import NEW_YORK
from app.services.route_search import CacheStatus, RouteSearchService
from app.services.suggestions.jobs import InvestigationRunner
from tests.fakes import FakeOtpServer, StaticSnapshots, fake_otp, make_request

DEPART = datetime.fromisoformat("2026-10-19T08:30:00-04:00")
FIXTURE_DAY = "2026-10-19"


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
    instance_a, instance_b = (
        RouteSearchService(
            fake_otp(server.handler), redis_client(redis_url), graph, StaticSnapshots(), config
        )
        for _ in range(2)
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
        route_search = RouteSearchService(
            otp, res.redis, GraphVersion(None), StaticSnapshots(), res.settings
        )
        investigations = InvestigationRunner(
            stub_investigation,
            res.redis,
            res.settings.key_hashing_secret.get_secret_value().encode(),
            max_concurrent=2,
            max_pending=4,
            timeout_s=5,
            ttl_s=60,
            poll_after_ms=100,
        )
        app.state.resources = dataclasses.replace(
            res, otp=otp, route_search=route_search, investigations=investigations
        )
        yield client


async def stub_investigation(
    request: RouteSearchRequest, baseline: Sequence[Route]
) -> InvestigationResult:
    """Pretends the first baseline route is a verified, faster suggestion.
    DEMO DATA: only exercises the API around investigations."""
    await asyncio.sleep(0.3)  # long enough to be cancelled mid-run
    proposal = TransferProposal(
        first_routes=["MTASBWY:2"], transfer_station="MTASBWY:120", second_routes=["MTASBWY:1"]
    )
    return InvestigationResult(
        applicable=True,
        generator="stub",
        suggestions=[
            VerifiedSuggestion(
                proposal=proposal, source="rules", route=baseline[0], saves_seconds=120
            )
        ],
        rejected=[
            RejectedProposal(proposal=proposal, source="rules", code="not_faster", detail="x")
        ],
    )


def wait_for(client: TestClient, investigation_id: str) -> dict[str, Any]:
    for _ in range(50):
        view = client.get(f"/api/v1/routes/investigations/{investigation_id}").json()
        if view["status"] not in ("queued", "running"):
            return dict(view)
        time.sleep(0.05)
    raise AssertionError("investigation did not finish")


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


def fixture_body(server: FakeOtpServer) -> dict[str, Any]:
    """A search two days ahead, with the recorded plan moved to that day."""
    day = (datetime.now(NEW_YORK) + timedelta(days=2)).date()
    server.payload = json.loads(json.dumps(server.payload).replace(FIXTURE_DAY, day.isoformat()))
    depart = datetime.combine(day, datetime.min.time(), NEW_YORK).replace(hour=8, minute=30)
    return search_body(depart_at=depart.isoformat())


def test_search_starts_one_shared_investigation_that_can_be_polled(
    client: TestClient, server: FakeOtpServer
) -> None:
    body = fixture_body(server)
    first = client.post("/api/v1/routes/search", json=body).json()
    second = client.post("/api/v1/routes/search", json=body).json()
    ref = first["investigation"]
    assert ref["status"] == "queued" and ref["poll_after_ms"] == 100
    assert second["investigation"]["id"] == ref["id"]  # identical searches share it

    view = wait_for(client, ref["id"])
    assert view["status"] == "completed"
    assert view["generator"] == "stub"
    assert view["suggestions"][0]["saves_seconds"] == 120
    assert view["rejected"] == {"not_faster": 1}


def test_cancelled_investigation_and_unknown_ids(client: TestClient, server: FakeOtpServer) -> None:
    ref = client.post("/api/v1/routes/search", json=fixture_body(server)).json()["investigation"]
    assert client.delete(f"/api/v1/routes/investigations/{ref['id']}").status_code == 204
    assert wait_for(client, ref["id"])["status"] == "cancelled"

    missing = client.get("/api/v1/routes/investigations/" + "0" * 24)
    assert missing.status_code == 404 and missing.json()["code"] == "not_found"
    assert client.delete("/api/v1/routes/investigations/" + "0" * 24).status_code == 404
    assert client.get("/api/v1/routes/investigations/not-an-id").status_code == 422
