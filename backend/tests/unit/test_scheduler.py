import asyncio

from app.ingestion.scheduler import ScheduledJob, run_scheduler


async def test_slow_job_does_not_block_others() -> None:
    stop = asyncio.Event()
    fast_runs = 0
    slow_started = asyncio.Event()

    async def slow() -> bool:
        slow_started.set()
        await asyncio.sleep(10)
        return True

    async def fast() -> bool:
        nonlocal fast_runs
        fast_runs += 1
        if fast_runs == 3:
            stop.set()
        return True

    jobs = [ScheduledJob("slow", 60, slow), ScheduledJob("fast", 0.01, fast)]
    scheduler = asyncio.create_task(run_scheduler(jobs, stop))
    await asyncio.wait_for(slow_started.wait(), 1)
    await asyncio.wait_for(stop.wait(), 15)
    scheduler.cancel()
    assert fast_runs >= 3


async def test_crashing_job_does_not_stop_scheduler() -> None:
    stop = asyncio.Event()
    attempts = 0

    async def crashing() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            stop.set()
        raise RuntimeError("boom")

    await asyncio.wait_for(run_scheduler([ScheduledJob("crash", 0.01, crashing)], stop), 15)
    assert attempts >= 2
