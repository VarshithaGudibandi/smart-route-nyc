import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cache.redis import ResilientRedis
from app.core.config import Settings
from app.db.models import (
    AccessibilityEquipmentStop,
    AccessibilityEvent,
    EquipmentStatus,
    FeedSyncRun,
    RouteFeedback,
    SourceFreshness,
)
from app.db.session import create_session_factory
from app.ingestion.retention import RetentionJob, RetentionPolicy
from app.ingestion.runner import run_job
from app.ingestion.sources.mta_elevators import MtaElevatorSource
from app.main import create_app
from app.services.accessibility.snapshot import ACCESSIBILITY_VERSION_KEY, SnapshotProvider
from tests.fakes import FIXTURES

BASE = "https://feeds.example/mta"


class MtaFeeds:
    """Serves the recorded elevator feeds; tests edit ``equipment``/``outages``."""

    def __init__(self) -> None:
        self.equipment: list[dict[str, Any]] = json.loads(
            (FIXTURES / "mta_ene_equipments.json").read_text()
        )
        self.outages: list[dict[str, Any]] = json.loads(
            (FIXTURES / "mta_ene_outages.json").read_text()
        )
        self.status: int | None = None
        self.calls = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.status is not None:
            return httpx.Response(self.status)
        if request.url.path.endswith("nyct_ene_equipments.json"):
            return httpx.Response(200, json=self.equipment)
        return httpx.Response(200, json=self.outages)


@pytest.fixture
async def sessions(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE accessibility_equipment, accessibility_equipment_stops, "
                "equipment_status, accessibility_events, source_freshness, feed_sync_runs, "
                "route_feedback CASCADE"
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
def feeds() -> MtaFeeds:
    return MtaFeeds()


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr("app.ingestion.http.asyncio.sleep", instant)


def elevator_source(
    feeds: MtaFeeds, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> MtaElevatorSource:
    http = httpx.AsyncClient(transport=httpx.MockTransport(feeds.handler))
    return MtaElevatorSource(http, sessions, redis, BASE)


async def statuses(sessions: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    async with sessions() as session:
        result = await session.execute(
            select(EquipmentStatus.equipment_external_id, EquipmentStatus.status)
        )
        return dict(result.all())


async def count(sessions: async_sessionmaker[AsyncSession], model: Any) -> int:
    async with sessions() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def run_statuses(sessions: async_sessionmaker[AsyncSession]) -> list[str]:
    async with sessions() as session:
        rows = await session.scalars(
            select(FeedSyncRun.status)
            .where(FeedSyncRun.feed_kind == "mta_elevators")
            .order_by(FeedSyncRun.started_at)
        )
        return list(rows)


async def version(redis: ResilientRedis) -> int:
    raw = await redis.get(ACCESSIBILITY_VERSION_KEY)
    return int(raw) if raw else 0


async def test_first_run_stores_inventory_and_current_outages_only(
    feeds: MtaFeeds, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    before = await version(redis)
    assert await run_job(elevator_source(feeds, sessions, redis), sessions, redis)

    current = await statuses(sessions)
    assert current["ES456"] == current["ES269"] == "out_of_service"
    # EL382 has only a *scheduled* outage; it is working now.
    assert current["EL382"] == "in_service"
    assert len(current) == 6
    assert await count(sessions, AccessibilityEquipmentStop) == 8  # two complexes map to 2 stops
    assert await count(sessions, AccessibilityEvent) == 6
    assert await version(redis) == before + 1


async def test_rerunning_identical_data_changes_nothing(
    feeds: MtaFeeds, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    source = elevator_source(feeds, sessions, redis)
    await run_job(source, sessions, redis)
    before = await version(redis)
    await run_job(source, sessions, redis)

    assert await run_statuses(sessions) == ["succeeded", "unchanged"]
    assert await count(sessions, AccessibilityEvent) == 6
    assert await version(redis) == before


async def test_cleared_outage_is_recorded_once(
    feeds: MtaFeeds, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    source = elevator_source(feeds, sessions, redis)
    await run_job(source, sessions, redis)
    before = await version(redis)

    feeds.outages = [o for o in feeds.outages if o["equipment"] != "ES456"]
    await run_job(source, sessions, redis)

    assert (await statuses(sessions))["ES456"] == "in_service"
    assert await count(sessions, AccessibilityEvent) == 7
    assert await version(redis) == before + 1


async def test_empty_feed_is_rejected_and_existing_data_kept(
    feeds: MtaFeeds, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    source = elevator_source(feeds, sessions, redis)
    await run_job(source, sessions, redis)

    feeds.equipment = []
    assert await run_job(source, sessions, redis) is False

    assert len(await statuses(sessions)) == 6
    async with sessions() as session:
        run = await session.scalar(select(FeedSyncRun).where(FeedSyncRun.status == "failed"))
        freshness = await session.get(SourceFreshness, "mta_elevators")
    assert run is not None and run.error_code == "empty_feed"
    assert freshness is not None and freshness.current_status == "failing"


async def test_transient_upstream_errors_are_retried(
    feeds: MtaFeeds, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    feeds.status = 404
    assert await run_job(elevator_source(feeds, sessions, redis), sessions, redis) is False
    assert feeds.calls == 1  # 404 is not retried

    feeds.status, feeds.calls = 503, 0
    assert await run_job(elevator_source(feeds, sessions, redis), sessions, redis) is False
    assert feeds.calls == 3  # retried up to the attempt limit


async def test_older_observation_never_overwrites_newer(
    feeds: MtaFeeds, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    source = elevator_source(feeds, sessions, redis)
    await run_job(source, sessions, redis)
    future = datetime.now(UTC) + timedelta(hours=1)
    async with sessions.begin() as session:
        await session.execute(
            update(EquipmentStatus)
            .where(EquipmentStatus.equipment_external_id == "EL223")
            .values(status="out_of_service", observed_at=future)
        )

    feeds.outages = feeds.outages[:1]  # change the payload so the run is not skipped
    await run_job(source, sessions, redis)
    assert (await statuses(sessions))["EL223"] == "out_of_service"


async def test_second_worker_skips_a_source_already_running(
    feeds: MtaFeeds, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    held = await redis.set_if_absent("srn:v1:ingest-lock:mta_elevators", "other-worker", 60_000)
    assert held
    try:
        assert await run_job(elevator_source(feeds, sessions, redis), sessions, redis)
        assert await run_statuses(sessions) == []
        assert feeds.calls == 0
    finally:
        await redis.delete_if_equals("srn:v1:ingest-lock:mta_elevators", "other-worker")


async def test_retention_deletes_only_expired_records(
    sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    now = datetime.now(UTC)
    old, recent = now - timedelta(days=800), now - timedelta(days=1)
    async with sessions.begin() as session:
        for fetched in (old, recent):
            session.add(
                AccessibilityEvent(
                    operator="MTA",
                    equipment_external_id=f"EL{uuid.uuid4().hex[:6]}",
                    event_type="status_change",
                    status="in_service",
                    fetched_at=fetched,
                )
            )
        session.add(FeedSyncRun(provider="mta", feed_kind="x", started_at=old, status="succeeded"))
        for created, comment in (
            (old, "ancient"),
            (now - timedelta(days=400), "old"),
            (recent, "new"),
        ):
            session.add(
                RouteFeedback(
                    route_result_ref_hash=uuid.uuid4().hex,
                    stars=4,
                    comment=comment,
                    created_at=created,
                )
            )

    settings = Settings(_env_file=None)
    assert await run_job(
        RetentionJob(sessions, RetentionPolicy.from_settings(settings)), sessions, redis
    )

    async with sessions() as session:
        comments = sorted(
            (c or "<cleared>") for c in await session.scalars(select(RouteFeedback.comment))
        )
    assert comments == ["<cleared>", "new"]
    assert await count(sessions, AccessibilityEvent) == 1
    assert await count(sessions, FeedSyncRun) == 1  # only the retention run itself


@pytest.fixture
def api(migrated_postgres_url: str, redis_url: str) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None, app_env="test", database_url=migrated_postgres_url, redis_url=redis_url
    )
    with TestClient(create_app(settings)) as client:
        yield client


async def test_freshness_endpoint_reports_ingested_sources(
    feeds: MtaFeeds,
    sessions: async_sessionmaker[AsyncSession],
    redis: ResilientRedis,
    api: TestClient,
) -> None:
    await run_job(elevator_source(feeds, sessions, redis), sessions, redis)

    body = api.get("/api/v1/data/freshness").json()
    by_source = {s["source"]: s for s in body["sources"]}
    assert by_source["mta_elevators"]["freshness"] == "fresh"
    assert by_source["mta_elevators"]["kind"] == "accessibility_status"
    assert by_source["mta_elevators"]["last_fetch"] == "ok"


async def test_snapshot_loads_inventory_and_reloads_when_status_changes(
    feeds: MtaFeeds,
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    redis: ResilientRedis,
) -> None:
    source = elevator_source(feeds, sessions, redis)
    await run_job(source, sessions, redis)
    provider = SnapshotProvider(engine, redis)

    snapshot = await provider.current()
    # EL223 is an elevator serving the 14 St complex (stops A31 and L01).
    assert [e.equipment_id for e in snapshot.elevators_by_stop["A31"]] == ["EL223"]
    assert snapshot.elevators_by_stop["L01"][0].in_service
    assert snapshot.observed_at is not None
    # Escalators are not step-free access and are excluded.
    assert all(
        e.equipment_id.startswith("EL") for es in snapshot.elevators_by_stop.values() for e in es
    )

    feeds.outages.append(
        {**feeds.outages[0], "equipment": "EL223", "isupcomingoutage": "N", "reason": "Repair"}
    )
    await run_job(source, sessions, redis)  # bumps the accessibility version

    reloaded = await provider.current()
    assert reloaded is not snapshot
    assert reloaded.elevators_by_stop["A31"][0].in_service is False
    assert reloaded.elevators_by_stop["A31"][0].reason == "Repair"


async def test_snapshot_is_reused_until_something_changes(
    feeds: MtaFeeds,
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    redis: ResilientRedis,
) -> None:
    await run_job(elevator_source(feeds, sessions, redis), sessions, redis)
    provider = SnapshotProvider(engine, redis)
    first = await provider.current()
    assert await provider.current() is first
