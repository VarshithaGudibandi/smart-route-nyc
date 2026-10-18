import dataclasses
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from testcontainers.community.redis import RedisContainer

from app.cache.redis import ResilientRedis
from app.core.config import Settings
from app.core.resilience import BreakerState
from app.integrations.otp.client import OtpClient
from app.main import create_app


def fake_otp(healthy: bool) -> OtpClient:
    """OTP stand-in: the real graph is too large to start in CI."""

    def handler(request: httpx.Request) -> httpx.Response:
        if not healthy:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"__typename": "QueryType"}})

    return OtpClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://otp")
    )


def make_client(database_url: str, redis_url: str, otp_healthy: bool) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=database_url,
        redis_url=redis_url,
        health_check_timeout_s=2.0,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        resources = app.state.resources
        app.state.resources = dataclasses.replace(resources, otp=fake_otp(otp_healthy))
        yield client


@pytest.fixture
def client(migrated_postgres_url: str, redis_url: str) -> Iterator[TestClient]:
    yield from make_client(migrated_postgres_url, redis_url, otp_healthy=True)


def statuses(body: dict[str, Any]) -> dict[str, str]:
    return {name: check["status"] for name, check in body["checks"].items()}


def test_ready_when_all_dependencies_are_up(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert statuses(response.json()) == {"database": "ok", "redis": "ok", "otp": "ok"}


def test_not_ready_when_routing_engine_is_down(migrated_postgres_url: str, redis_url: str) -> None:
    for client in make_client(migrated_postgres_url, redis_url, otp_healthy=False):
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        assert statuses(response.json())["otp"] == "unavailable"


def test_not_ready_when_database_is_unreachable(redis_url: str) -> None:
    unreachable = "postgresql+asyncpg://user:pass@127.0.0.1:1/none"
    for client in make_client(unreachable, redis_url, otp_healthy=True):
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        assert statuses(response.json()) == {"database": "unavailable", "redis": "ok", "otp": "ok"}


async def test_redis_outage_degrades_to_cache_misses() -> None:
    """Stop a live Redis mid-test: reads miss, the breaker opens, nothing raises."""
    with RedisContainer("redis:7.4-alpine") as container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
        redis = ResilientRedis.from_url(
            url, timeout_ms=200, failure_threshold=2, reset_timeout_s=60
        )
        assert await redis.set("route:abc", b"cached", ttl_s=30)
        assert await redis.get("route:abc") == b"cached"

        container.get_wrapped_container().stop()

        assert await redis.get("route:abc") is None
        assert await redis.get("route:abc") is None
        assert redis.breaker.state is BreakerState.OPEN
        assert await redis.set("route:abc", b"new", ttl_s=30) is False
        assert await redis.ping() is False
        await redis.close()
