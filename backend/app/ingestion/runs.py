"""Run bookkeeping shared by every ingestion source.

Each source run is recorded in ``feed_sync_runs`` (started, then finished or
failed), and a successful run updates ``source_freshness``. A Redis lock
keeps two worker instances from processing the same source at once; the
upserts are idempotent anyway, so the lock saves work rather than
protecting correctness.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.redis import ResilientRedis
from app.core.freshness import SourceKind
from app.db.models import FeedSyncRun, SourceFreshness


@dataclass
class RunOutcome:
    """What a source reports back after a successful run."""

    checksum: str | None = None
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    unchanged: bool = False
    stats: dict[str, int] = field(default_factory=dict)


@dataclass
class RunContext:
    run_id: uuid.UUID
    previous_checksum: str | None


class SourceLockedError(Exception):
    """Another worker is already running this source."""


@asynccontextmanager
async def source_lock(redis: ResilientRedis, source: str, ttl_ms: int) -> AsyncIterator[None]:
    key = f"srn:v1:ingest-lock:{source}"
    token = uuid.uuid4().hex
    acquired = await redis.set_if_absent(key, token, ttl_ms)
    if acquired is False:
        raise SourceLockedError(source)
    try:
        yield
    finally:
        if acquired:
            await redis.delete_if_equals(key, token)


async def start_run(
    sessions: async_sessionmaker[AsyncSession], provider: str, source_key: str
) -> RunContext:
    async with sessions.begin() as session:
        previous = await session.scalar(
            select(FeedSyncRun.checksum)
            .where(
                FeedSyncRun.provider == provider,
                FeedSyncRun.feed_kind == source_key,
                FeedSyncRun.status.in_(("succeeded", "unchanged")),
                FeedSyncRun.checksum.is_not(None),
            )
            .order_by(FeedSyncRun.started_at.desc())
            .limit(1)
        )
        run = FeedSyncRun(
            provider=provider,
            feed_kind=source_key,
            started_at=datetime.now(UTC),
            status="running",
        )
        session.add(run)
    return RunContext(run_id=run.run_id, previous_checksum=previous)


async def finish_run(
    sessions: async_sessionmaker[AsyncSession],
    ctx: RunContext,
    source_key: str,
    kind: SourceKind | None,
    outcome: RunOutcome,
) -> None:
    """Mark the run finished and, for data sources, record freshness."""
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        await session.execute(
            update(FeedSyncRun)
            .where(FeedSyncRun.run_id == ctx.run_id)
            .values(
                finished_at=now,
                status="unchanged" if outcome.unchanged else "succeeded",
                checksum=outcome.checksum,
            )
        )
        if kind is None:
            return
        values = {
            "source_key": source_key,
            "kind": kind.value,
            "source_version": outcome.checksum,
            "observed_at": outcome.observed_at or now,
            "fetched_at": now,
            "current_status": "ok",
            "valid_until": outcome.valid_until,
            "last_successful_run_id": ctx.run_id,
        }
        stmt = insert(SourceFreshness).values(**values)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[SourceFreshness.source_key],
                set_={k: stmt.excluded[k] for k in values if k != "source_key"},
            )
        )


async def fail_run(
    sessions: async_sessionmaker[AsyncSession],
    ctx: RunContext,
    source_key: str,
    kind: SourceKind | None,
    error_code: str,
) -> None:
    """Record a failure. Data from the last good run is kept; its age keeps
    growing, so readers see it become stale rather than silently trusted."""
    async with sessions.begin() as session:
        await session.execute(
            update(FeedSyncRun)
            .where(FeedSyncRun.run_id == ctx.run_id)
            .values(finished_at=datetime.now(UTC), status="failed", error_code=error_code[:64])
        )
        if kind is None:
            return
        stmt = insert(SourceFreshness).values(
            source_key=source_key, kind=kind.value, current_status="failing"
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[SourceFreshness.source_key],
                set_={"current_status": "failing"},
            )
        )
