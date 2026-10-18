"""Ingestion worker entry point.

python -m app.ingestion            # run continuously
python -m app.ingestion --once     # run every job once, then exit
python -m app.ingestion --once --job mta_elevators
"""

import argparse
import asyncio
import functools
import signal
import sys
import time

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.redis import ResilientRedis
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.metrics import serve_metrics
from app.db.session import create_engine, create_session_factory
from app.ingestion.retention import RetentionJob, RetentionPolicy
from app.ingestion.runner import Job, run_job
from app.ingestion.scheduler import ScheduledJob, run_scheduler
from app.ingestion.sources.mta_alerts import MtaAlertsSource
from app.ingestion.sources.mta_elevators import MtaElevatorSource
from app.ingestion.sources.realtime import GtfsRealtimeProbe
from app.ingestion.sources.static_gtfs import StaticGtfsMonitor

logger = structlog.get_logger("app.ingestion")

HEARTBEAT_KEY = "srn:v1:worker:heartbeat"
HEARTBEAT_TTL_S = 120
USER_AGENT = "SmartRouteNYC-ingestion/0.1 (+https://github.com/VarshithaGudibandi/smart-route-nyc)"


def build_jobs(
    settings: Settings,
    http: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    redis: ResilientRedis,
) -> list[tuple[Job, float]]:
    """Every job with its run interval in seconds."""
    base = settings.mta_feed_base_url
    realtime_every = settings.ingest_realtime_interval_s
    return [
        (MtaElevatorSource(http, sessions, redis, base), settings.ingest_elevators_interval_s),
        (MtaAlertsSource(http, sessions, redis, base), settings.ingest_alerts_interval_s),
        (
            GtfsRealtimeProbe(http, "mta", "mta_subway_realtime", f"{base}/nyct%2Fgtfs"),
            realtime_every,
        ),
        (
            GtfsRealtimeProbe(http, "path", "path_realtime", settings.path_gtfs_rt_url),
            realtime_every,
        ),
        (
            GtfsRealtimeProbe(http, "mta", "mta_bus_realtime", settings.mta_bus_gtfs_rt_url),
            realtime_every,
        ),
        (
            StaticGtfsMonitor(sessions, settings.gtfs_manifest_file),
            settings.ingest_static_interval_s,
        ),
        (
            RetentionJob(sessions, RetentionPolicy.from_settings(settings)),
            settings.ingest_retention_interval_s,
        ),
    ]


async def main(once: bool, only: str | None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    redis = ResilientRedis.from_url(
        str(settings.redis_url),
        timeout_ms=settings.redis_timeout_ms,
        failure_threshold=settings.redis_breaker_failure_threshold,
        reset_timeout_s=settings.redis_breaker_reset_s,
    )
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.ingest_fetch_timeout_s),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    jobs = build_jobs(settings, http, sessions, redis)
    if only is not None:
        jobs = [(job, every) for job, every in jobs if job.source_key == only]
        if not jobs:
            logger.error("unknown_job", job=only)
            return 2

    try:
        if once:
            results = [await run_job(job, sessions, redis) for job, _ in jobs]
            return 0 if all(results) else 1

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)

        async def heartbeat() -> None:
            await redis.set(HEARTBEAT_KEY, str(int(time.time())).encode(), ttl_s=HEARTBEAT_TTL_S)

        scheduled = [
            ScheduledJob(
                name=job.source_key,
                interval_s=every,
                run=functools.partial(run_job, job, sessions, redis),
            )
            for job, every in jobs
        ]
        serve_metrics(settings.metrics_port)
        logger.info("worker_started", jobs=[job.name for job in scheduled])
        await run_scheduler(scheduled, stop, on_tick=heartbeat)
        logger.info("worker_stopped")
        return 0
    finally:
        await http.aclose()
        await redis.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SmartRouteNYC ingestion worker")
    parser.add_argument("--once", action="store_true", help="run each job once and exit")
    parser.add_argument("--job", help="run only this job (its source key)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.once, args.job)))
