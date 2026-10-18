import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from app.cache.redis import ResilientRedis
from app.schemas.routes import Route, RouteSearchRequest
from app.schemas.suggestions import InvestigationResult
from app.services.suggestions.jobs import InvestigationRunner, Record
from tests.fakes import in_memory_redis, make_request
from tests.unit.test_redis_resilience import FailingRedis, make

REQUESTED = datetime.fromisoformat("2026-10-19T08:30:00-04:00")
RESULT = InvestigationResult(applicable=True, generator="test")


class Investigation:
    """Controllable stand-in for ``SuggestionService.investigate``."""

    def __init__(self, delay_s: float = 0.0, error: Exception | None = None) -> None:
        self.delay_s = delay_s
        self.error = error
        self.calls = 0
        self.cancelled = False

    async def __call__(
        self, request: RouteSearchRequest, baseline: Sequence[Route]
    ) -> InvestigationResult:
        self.calls += 1
        try:
            await asyncio.sleep(self.delay_s)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error:
            raise self.error
        return RESULT


def runner(
    investigate: Investigation,
    redis: ResilientRedis | None = None,
    max_pending: int = 4,
    timeout_s: float = 2.0,
    recorded: list[tuple[str, InvestigationResult]] | None = None,
) -> InvestigationRunner:
    async def record(graph: str, result: InvestigationResult) -> None:
        if recorded is not None:
            recorded.append((graph, result))

    return InvestigationRunner(
        investigate,
        redis or in_memory_redis(),
        b"secret",
        max_concurrent=1,
        max_pending=max_pending,
        timeout_s=timeout_s,
        ttl_s=60,
        poll_after_ms=500,
        record_outcome=record,
    )


async def start(jobs: InvestigationRunner, key: str = "search-key") -> str:
    ref = await jobs.start(key, make_request(REQUESTED), REQUESTED, [], "graph-1")
    assert ref is not None
    return ref.id


async def settle(jobs: InvestigationRunner) -> None:
    for _ in range(100):
        if not jobs.pending:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("tasks did not finish")


async def test_runs_in_background_and_records_outcomes() -> None:
    recorded: list[tuple[str, InvestigationResult]] = []
    jobs = runner(Investigation(delay_s=0.05), recorded=recorded)
    ref = await jobs.start("search-key", make_request(REQUESTED), REQUESTED, [], "graph-1")
    assert ref is not None and ref.status == "queued" and ref.poll_after_ms == 500

    await settle(jobs)
    view = await jobs.view(ref.id)
    assert view is not None and view.status == "completed" and view.generator == "test"
    assert recorded == [("graph-1", RESULT)]


async def test_identical_searches_share_one_investigation() -> None:
    investigate = Investigation(delay_s=0.05)
    jobs = runner(investigate)
    first, second = await start(jobs), await start(jobs)
    await settle(jobs)
    assert first == second and investigate.calls == 1
    # Another departure minute is another investigation.
    other = await jobs.start(
        "search-key", make_request(REQUESTED), REQUESTED + timedelta(minutes=1), [], "g"
    )
    assert other is not None and other.id != first


async def test_a_second_instance_reuses_the_first_ones_investigation() -> None:
    redis = in_memory_redis()
    investigate_a, investigate_b = Investigation(delay_s=0.05), Investigation()
    a, b = runner(investigate_a, redis), runner(investigate_b, redis)
    assert await start(a) == await start(b)
    await settle(a)
    assert (investigate_a.calls, investigate_b.calls) == (1, 0)
    view = await b.view(await start(b))
    assert view is not None and view.status == "completed"


async def test_sheds_load_beyond_the_pending_limit() -> None:
    jobs = runner(Investigation(delay_s=0.2), max_pending=1)
    await start(jobs, "a")
    assert await jobs.start("b", make_request(REQUESTED), REQUESTED, [], "g") is None
    await jobs.close()


@pytest.mark.parametrize(
    ("investigate", "reason"),
    [
        (Investigation(delay_s=1.0), "Timed out."),
        (Investigation(error=RuntimeError("bug")), "The investigation failed."),
    ],
)
async def test_failures_are_reported_not_raised(investigate: Investigation, reason: str) -> None:
    jobs = runner(investigate, timeout_s=0.05)
    investigation_id = await start(jobs)
    await settle(jobs)
    view = await jobs.view(investigation_id)
    assert view is not None and (view.status, view.reason) == ("failed", reason)


async def test_cancel_stops_the_task_and_keeps_the_result_out() -> None:
    investigate = Investigation(delay_s=1.0)
    recorded: list[tuple[str, InvestigationResult]] = []
    jobs = runner(investigate, recorded=recorded)
    investigation_id = await start(jobs)
    await asyncio.sleep(0.02)

    assert await jobs.cancel(investigation_id)
    await settle(jobs)
    view = await jobs.view(investigation_id)
    assert view is not None and view.status == "cancelled"
    assert investigate.cancelled and recorded == []
    assert not await jobs.cancel("0" * 24)


async def test_investigation_abandoned_by_a_stopped_instance_reads_as_failed() -> None:
    redis = in_memory_redis()
    jobs = runner(Investigation(), redis)
    stale = Record(
        status="running",
        deadline=datetime.now(UTC) - timedelta(seconds=1),
        updated_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    await redis.set("srn:v1:investigation:" + "a" * 24, stale.model_dump_json().encode(), 60)
    view = await jobs.view("a" * 24)
    assert view is not None and view.status == "failed"
    assert view.reason == "The investigation did not finish."


async def test_no_investigation_without_redis() -> None:
    investigate = Investigation()
    jobs = runner(investigate, make(FailingRedis()))
    assert await jobs.start("k", make_request(REQUESTED), REQUESTED, [], "g") is None
    assert investigate.calls == 0
