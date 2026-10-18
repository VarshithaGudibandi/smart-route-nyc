import copy
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.redis import ResilientRedis
from app.db.models import FeedSyncRun, ServiceAlert, SourceFreshness
from app.ingestion.runner import run_job
from app.ingestion.sources.mta_alerts import ALERTS_VERSION_KEY, MtaAlertsSource
from tests.fakes import FIXTURES

pytestmark = pytest.mark.usefixtures("no_backoff")

DELAY_ALERT = "lmm:alert:272416:26"


class AlertFeed:
    def __init__(self) -> None:
        self.feed: dict[str, Any] = json.loads((FIXTURES / "mta_subway_alerts.json").read_text())
        self.body: bytes | None = None

    def tick(self, seconds: int = 60) -> None:
        """A later fetch of the same feed: only the header timestamp moves."""
        self.feed = copy.deepcopy(self.feed)
        self.feed["header"]["timestamp"] += seconds

    async def handler(self, request: httpx.Request) -> httpx.Response:
        assert "subway-alerts.json" in str(request.url)
        return httpx.Response(200, content=self.body or json.dumps(self.feed).encode())


def source(
    feed: AlertFeed, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> MtaAlertsSource:
    http = httpx.AsyncClient(transport=httpx.MockTransport(feed.handler))
    return MtaAlertsSource(http, sessions, redis, "https://feeds.example/mta")


async def stored(sessions: async_sessionmaker[AsyncSession]) -> dict[str, ServiceAlert]:
    async with sessions() as session:
        return {a.alert_external_id: a for a in await session.scalars(select(ServiceAlert))}


async def version(redis: ResilientRedis) -> int:
    raw = await redis.get(ALERTS_VERSION_KEY)
    return int(raw) if raw else 0


async def test_stores_alerts_with_type_text_and_entities(
    sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    feed = AlertFeed()
    before = await version(redis)
    assert await run_job(source(feed, sessions, redis), sessions, redis)

    alerts = await stored(sessions)
    assert len(alerts) == 6
    delay = alerts[DELAY_ALERT]
    assert delay.alert_type == "Delays"
    assert delay.header_text.startswith("Downtown [4][6] trains are delayed")
    assert {e["route_id"] for e in delay.informed_entities} == {"4", "6"}
    assert delay.active_periods == [[1792335220, None]]
    assert await version(redis) == before + 1


async def test_new_header_timestamp_alone_is_unchanged_but_refreshes_freshness(
    sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    feed = AlertFeed()
    job = source(feed, sessions, redis)
    await run_job(job, sessions, redis)
    before = await version(redis)

    feed.tick()
    await run_job(job, sessions, redis)

    async with sessions() as session:
        runs = list(
            await session.scalars(select(FeedSyncRun.status).order_by(FeedSyncRun.started_at))
        )
        freshness = await session.get(SourceFreshness, "mta_subway_alerts")
    assert runs == ["succeeded", "unchanged"]
    assert await version(redis) == before
    assert freshness is not None and freshness.observed_at is not None
    assert int(freshness.observed_at.timestamp()) == feed.feed["header"]["timestamp"]


async def test_alert_removed_from_feed_is_deleted(
    sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    feed = AlertFeed()
    job = source(feed, sessions, redis)
    await run_job(job, sessions, redis)

    feed.tick()
    feed.feed["entity"] = [e for e in feed.feed["entity"] if e["id"] != DELAY_ALERT]
    await run_job(job, sessions, redis)

    alerts = await stored(sessions)
    assert DELAY_ALERT not in alerts and len(alerts) == 5


async def test_older_feed_does_not_roll_back(
    sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    feed = AlertFeed()
    job = source(feed, sessions, redis)
    feed.tick(600)
    await run_job(job, sessions, redis)

    feed.tick(-300)
    feed.feed["entity"] = feed.feed["entity"][:1]
    assert await run_job(job, sessions, redis)
    assert len(await stored(sessions)) == 6


@pytest.mark.parametrize("body", [b"<html>maintenance</html>", b'{"entity": []}'])
async def test_malformed_feed_fails_and_keeps_alerts(
    body: bytes, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    feed = AlertFeed()
    job = source(feed, sessions, redis)
    await run_job(job, sessions, redis)

    feed.body = body
    assert await run_job(job, sessions, redis) is False
    assert len(await stored(sessions)) == 6
    async with sessions() as session:
        freshness = await session.get(SourceFreshness, "mta_subway_alerts")
    assert freshness is not None and freshness.current_status == "failing"
