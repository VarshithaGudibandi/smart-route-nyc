"""Liveness and readiness probes.

``live`` answers whether the process is running and never touches
dependencies. ``ready`` checks PostgreSQL, Redis and OpenTripPlanner in
parallel, each with its own timeout, and returns 503 if any is unavailable.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text

from app.api.deps import Resources, get_resources
from app.schemas.health import DependencyCheck, LiveResponse, ReadyResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> LiveResponse:
    return LiveResponse()


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyResponse}},
)
async def ready(
    response: Response, resources: Annotated[Resources, Depends(get_resources)]
) -> ReadyResponse:
    timeout = resources.settings.health_check_timeout_s

    async def database() -> bool:
        async with resources.db_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    names = ["database", "redis", "otp"]
    results = await asyncio.gather(
        _timed(database, timeout),
        _timed(resources.redis.ping, timeout),
        _timed(lambda: resources.otp.is_ready(timeout), timeout),
    )
    checks = dict(zip(names, results, strict=True))
    all_ok = all(check.status == "ok" for check in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ready" if all_ok else "not_ready", checks=checks)


async def _timed(check: Callable[[], Awaitable[bool]], timeout_s: float) -> DependencyCheck:
    started = time.perf_counter()
    try:
        ok = await asyncio.wait_for(check(), timeout=timeout_s)
    except Exception:  # Any failure means "unavailable"; details belong in the logs.
        ok = False
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return DependencyCheck(status="ok" if ok else "unavailable", latency_ms=latency_ms)
