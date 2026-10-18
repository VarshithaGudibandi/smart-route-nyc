"""Run suggestion investigations in the background after a search.

A search returns at once with an ``investigation`` reference; the
investigation (timetable lookups and up to four OTP verifications, about a
second) runs on the API instance's event loop and the client polls for it.

* Bounded: at most ``max_concurrent`` run at once and ``max_pending``
  exist on an instance. Beyond that, a search simply gets no
  investigation (load shedding); the search itself is never slowed.
* Shared: the ID is derived from the search's cache key and departure
  minute, so identical searches, on any instance, share one investigation.
  Creation is a Redis SET NX, so exactly one instance runs it.
* Stateless to poll: state lives in Redis with a TTL, so any instance can
  answer a poll. An investigation whose deadline passed while still queued
  or running (its instance stopped) is reported as failed.
* Cancellable: DELETE marks it cancelled; a running investigation checks
  the mark before storing its result, and a local task is also cancelled.

Without Redis there is nowhere to share state, so no investigation starts.
"""

import asyncio
import hashlib
import hmac
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

import structlog
from pydantic import BaseModel, ValidationError

from app.cache.redis import ResilientRedis
from app.core.metrics import (
    INVESTIGATION_DURATION,
    INVESTIGATIONS,
    INVESTIGATIONS_RUNNING,
    SUGGESTIONS_VERIFIED,
)
from app.schemas.routes import InvestigationRef, InvestigationState, Route, RouteSearchRequest
from app.schemas.suggestions import TERMINAL_STATES, InvestigationResult, InvestigationView

logger = structlog.get_logger(__name__)

KEY_PREFIX = "srn:v1:investigation"

Investigate = Callable[[RouteSearchRequest, Sequence[Route]], Awaitable[InvestigationResult]]
RecordOutcome = Callable[[str, InvestigationResult], Awaitable[None]]


class Record(BaseModel):
    status: InvestigationState
    deadline: datetime
    updated_at: datetime
    result: InvestigationResult | None = None
    reason: str | None = None


class InvestigationRunner:
    def __init__(
        self,
        investigate: Investigate,
        redis: ResilientRedis,
        secret: bytes,
        *,
        max_concurrent: int,
        max_pending: int,
        timeout_s: float,
        ttl_s: int,
        poll_after_ms: int,
        record_outcome: RecordOutcome | None = None,
    ) -> None:
        self._investigate = investigate
        self._redis = redis
        self._secret = secret
        self._slots = asyncio.Semaphore(max_concurrent)
        self._max_pending = max_pending
        self._timeout_s = timeout_s
        self._ttl_s = ttl_s
        self._poll_after_ms = poll_after_ms
        self._record_outcome = record_outcome
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def pending(self) -> int:
        return len(self._tasks)

    def investigation_id(self, search_key: str, requested: datetime) -> str:
        material = f"{search_key}|{requested.astimezone(UTC):%Y%m%dT%H%M}"
        return hmac.new(self._secret, material.encode(), hashlib.sha256).hexdigest()[:24]

    async def start(
        self,
        search_key: str,
        request: RouteSearchRequest,
        requested: datetime,
        baseline: Sequence[Route],
        graph_version: str,
    ) -> InvestigationRef | None:
        investigation_id = self.investigation_id(search_key, requested)
        existing = await self._read(investigation_id)
        if existing is not None:
            INVESTIGATIONS.labels("deduplicated").inc()
            return self._ref(investigation_id, existing.status)
        if self.pending >= self._max_pending:
            INVESTIGATIONS.labels("shed").inc()
            logger.info("investigation_shed", pending=self.pending)
            return None

        now = datetime.now(UTC)
        record = Record(
            status="queued",
            deadline=now + timedelta(seconds=self._timeout_s * 2),
            updated_at=now,
        )
        created = await self._redis.set_if_absent(
            self._key(investigation_id), record.model_dump_json(), self._ttl_s * 1000
        )
        if created is None:
            return None
        if created is False:  # another instance won the race
            INVESTIGATIONS.labels("deduplicated").inc()
            return self._ref(investigation_id, "queued")

        INVESTIGATIONS.labels("started").inc()

        task = asyncio.create_task(
            self._run(investigation_id, request, list(baseline), graph_version, record.deadline)
        )
        self._tasks[investigation_id] = task
        INVESTIGATIONS_RUNNING.inc()
        task.add_done_callback(lambda _: self._finished(investigation_id))
        return self._ref(investigation_id, "queued")

    async def view(self, investigation_id: str) -> InvestigationView | None:
        record = await self._read(investigation_id)
        if record is None:
            return None
        if record.status not in TERMINAL_STATES and datetime.now(UTC) > record.deadline:
            record = record.model_copy(
                update={"status": "failed", "reason": "The investigation did not finish."}
            )
        result = record.result
        return InvestigationView(
            id=investigation_id,
            status=record.status,
            generator=result.generator if result else None,
            applicable=result.applicable if result else None,
            reason=(result.reason if result else None) or record.reason,
            suggestions=result.suggestions if result else [],
            rejected=dict(Counter(r.code for r in result.rejected)) if result else {},
            updated_at=record.updated_at,
        )

    async def suggestion_routes(self, investigation_id: str) -> list[Route]:
        view = await self.view(investigation_id)
        return [s.route for s in view.suggestions] if view else []

    async def cancel(self, investigation_id: str) -> bool:
        record = await self._read(investigation_id)
        if record is None:
            return False
        await self._redis.set(self._cancel_key(investigation_id), b"1", ttl_s=self._ttl_s)
        if record.status not in TERMINAL_STATES:
            await self._write(investigation_id, record, "cancelled")
        if task := self._tasks.get(investigation_id):
            task.cancel()
        return True

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(
        self,
        investigation_id: str,
        request: RouteSearchRequest,
        baseline: list[Route],
        graph_version: str,
        deadline: datetime,
    ) -> None:
        log = logger.bind(investigation_id=investigation_id)
        record = Record(status="queued", deadline=deadline, updated_at=datetime.now(UTC))
        try:
            async with self._slots:
                if await self._cancelled(investigation_id):
                    INVESTIGATIONS.labels("cancelled").inc()
                    return
                await self._write(investigation_id, record, "running")
                started = time.perf_counter()
                result = await asyncio.wait_for(
                    self._investigate(request, baseline), self._timeout_s
                )
                INVESTIGATION_DURATION.observe(time.perf_counter() - started)
            if await self._cancelled(investigation_id):
                INVESTIGATIONS.labels("cancelled").inc()
                return
            await self._write(investigation_id, record, "completed", result=result)
            INVESTIGATIONS.labels("completed").inc()
            SUGGESTIONS_VERIFIED.inc(len(result.suggestions))
            if self._record_outcome is not None:
                await self._record_outcome(graph_version, result)
        except asyncio.CancelledError:
            INVESTIGATIONS.labels("cancelled").inc()
            log.info("investigation_cancelled")
            raise
        except TimeoutError:
            INVESTIGATIONS.labels("timed_out").inc()
            log.warning("investigation_timed_out")
            await self._write(investigation_id, record, "failed", reason="Timed out.")
        except Exception:
            INVESTIGATIONS.labels("failed").inc()
            log.exception("investigation_crashed")
            await self._write(
                investigation_id, record, "failed", reason="The investigation failed."
            )

    def _finished(self, investigation_id: str) -> None:
        self._tasks.pop(investigation_id, None)
        INVESTIGATIONS_RUNNING.dec()

    async def _cancelled(self, investigation_id: str) -> bool:
        return await self._redis.get(self._cancel_key(investigation_id)) is not None

    async def _write(
        self,
        investigation_id: str,
        record: Record,
        status: InvestigationState,
        result: InvestigationResult | None = None,
        reason: str | None = None,
    ) -> None:
        updated = record.model_copy(
            update={
                "status": status,
                "result": result,
                "reason": reason,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._redis.set(
            self._key(investigation_id), updated.model_dump_json().encode(), ttl_s=self._ttl_s
        )

    async def _read(self, investigation_id: str) -> Record | None:
        raw = await self._redis.get(self._key(investigation_id))
        if raw is None:
            return None
        try:
            return Record.model_validate_json(raw)
        except ValidationError:
            logger.warning("investigation_record_invalid")
            return None

    def _ref(self, investigation_id: str, status: InvestigationState) -> InvestigationRef:
        return InvestigationRef(
            id=investigation_id,
            status=status,
            poll_after_ms=0 if status in TERMINAL_STATES else self._poll_after_ms,
        )

    def _key(self, investigation_id: str) -> str:
        return f"{KEY_PREFIX}:{investigation_id}"

    def _cancel_key(self, investigation_id: str) -> str:
        return f"{KEY_PREFIX}:{investigation_id}:cancel"
