"""Real PostgreSQL and Redis in containers, shared across the test session."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from app.cache.redis import ResilientRedis
from app.db.session import create_session_factory

BACKEND_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:17-alpine", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7.4-alpine") as redis:
        host = redis.get_container_host_ip()
        port = redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session")
def migrated_postgres_url(postgres_url: str) -> str:
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def engine(migrated_postgres_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_postgres_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessions(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE accessibility_equipment, accessibility_equipment_stops, "
                "equipment_status, accessibility_events, source_freshness, feed_sync_runs, "
                "route_feedback, service_alerts, suggestion_outcomes CASCADE"
            )
        )
    yield create_session_factory(engine)


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[ResilientRedis]:
    client = ResilientRedis.from_url(
        redis_url, timeout_ms=500, failure_threshold=3, reset_timeout_s=10
    )
    yield client
    await client.close()


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr("app.ingestion.http.asyncio.sleep", instant)
