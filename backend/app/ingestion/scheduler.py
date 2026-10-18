"""Interval scheduler for ingestion jobs.

Each job runs on its own interval with random jitter, in its own task, so a
slow or failing source never delays the others. A job is never run twice
concurrently in this process (the Redis lock covers other processes).
"""

import asyncio
import contextlib
import random
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

JITTER_FRACTION = 0.1


@dataclass
class ScheduledJob:
    name: str
    interval_s: float
    run: Callable[[], Coroutine[Any, Any, bool]]


async def run_scheduler(
    jobs: list[ScheduledJob],
    stop: asyncio.Event,
    on_tick: Callable[[], Awaitable[None]] | None = None,
) -> None:
    next_due = {job.name: time.monotonic() for job in jobs}
    running: dict[str, asyncio.Task[bool]] = {}

    while not stop.is_set():
        now = time.monotonic()
        for job in jobs:
            if job.name in running or now < next_due[job.name]:
                continue
            running[job.name] = asyncio.create_task(job.run(), name=job.name)
            jitter = random.uniform(-JITTER_FRACTION, JITTER_FRACTION) * job.interval_s
            next_due[job.name] = now + job.interval_s + jitter

        for name in [n for n, task in running.items() if task.done()]:
            task = running.pop(name)
            if task.exception() is not None:
                logger.error("job_task_crashed", job=name, error=repr(task.exception()))

        if on_tick is not None:
            await on_tick()
        wait = max(0.0, min(next_due.values()) - time.monotonic())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=min(wait, 5.0))

    if running:
        logger.info("waiting_for_running_jobs", jobs=sorted(running))
        await asyncio.gather(*running.values(), return_exceptions=True)
