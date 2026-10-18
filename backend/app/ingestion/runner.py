"""Run one ingestion job: lock, record, execute, record the outcome."""

import time
from typing import Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.redis import ResilientRedis
from app.core.freshness import SourceKind
from app.core.metrics import INGEST_DURATION, INGEST_LAST_SUCCESS, INGEST_RUNS
from app.ingestion.http import FetchError
from app.ingestion.runs import (
    RunContext,
    RunOutcome,
    SourceLockedError,
    fail_run,
    finish_run,
    source_lock,
    start_run,
)

logger = structlog.get_logger(__name__)


class Job(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def source_key(self) -> str: ...

    @property
    def kind(self) -> SourceKind | None:
        """None for maintenance jobs that are not data sources (no freshness row)."""
        ...

    async def run(self, ctx: RunContext) -> RunOutcome: ...


async def run_job(
    job: Job,
    sessions: async_sessionmaker[AsyncSession],
    redis: ResilientRedis,
    lock_ttl_ms: int = 300_000,
) -> bool:
    """Run ``job`` once. Returns False on failure; failures are recorded, not raised."""
    started = time.perf_counter()
    log = logger.bind(source=job.source_key)
    try:
        async with source_lock(redis, job.source_key, lock_ttl_ms):
            ctx = await start_run(sessions, job.provider, job.source_key)
            try:
                outcome = await job.run(ctx)
            except FetchError as exc:
                await fail_run(sessions, ctx, job.source_key, job.kind, exc.code)
                INGEST_RUNS.labels(job.source_key, "failed").inc()
                log.warning("ingest_failed", error_code=exc.code, error=str(exc))
                return False
            except Exception as exc:
                await fail_run(sessions, ctx, job.source_key, job.kind, type(exc).__name__)
                INGEST_RUNS.labels(job.source_key, "crashed").inc()
                log.exception("ingest_crashed")
                return False
            await finish_run(sessions, ctx, job.source_key, job.kind, outcome)
    except SourceLockedError:
        INGEST_RUNS.labels(job.source_key, "skipped_locked").inc()
        log.info("ingest_skipped_locked")
        return True

    elapsed = time.perf_counter() - started
    INGEST_RUNS.labels(job.source_key, "unchanged" if outcome.unchanged else "updated").inc()
    INGEST_DURATION.labels(job.source_key).observe(elapsed)
    INGEST_LAST_SUCCESS.labels(job.source_key).set_to_current_time()
    log.info(
        "ingest_succeeded",
        unchanged=outcome.unchanged,
        duration_ms=round(elapsed * 1000, 1),
        **outcome.stats,
    )
    return True
