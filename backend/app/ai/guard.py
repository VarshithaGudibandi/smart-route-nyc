"""Operational guard around every language-model call.

* A timeout per call, and one retry for transient provider errors.
* A circuit breaker, so a failing provider is not called on every request.
* A daily call budget shared across instances (Redis), capping spend.
* Structured logs with latency and token counts, never prompt contents.
"""

import asyncio
import time
from datetime import UTC, datetime
from typing import Protocol

import structlog

from app.ai.provider import LlmProvider, LlmResponse, LlmUnavailableError
from app.cache.redis import ResilientRedis
from app.core.resilience import CircuitBreaker, CircuitOpenError

logger = structlog.get_logger(__name__)

DAY_S = 86_400


class TransientLlmError(Exception):
    """Providers raise this for errors worth one retry (rate limits, 5xx)."""


class UsageBudget(Protocol):
    async def spend(self) -> None:
        """Account for one call; raise ``LlmUnavailableError`` if over budget."""
        ...


class DailyCallBudget:
    """Calls per UTC day, shared across instances through Redis."""

    def __init__(self, redis: ResilientRedis, limit: int) -> None:
        self._redis = redis
        self._limit = limit

    async def spend(self) -> None:
        day = datetime.now(UTC).strftime("%Y%m%d")
        count = await self._redis.increment_window(f"srn:v1:llm-budget:{day}", DAY_S)
        # Without Redis the shared budget cannot be enforced; fail closed on spend.
        if count is None:
            raise LlmUnavailableError("Usage budget cannot be checked", "ai_budget_unavailable")
        if count > self._limit:
            raise LlmUnavailableError("Daily language-model budget reached", "ai_budget_exhausted")


class UnlimitedBudget:
    """For offline evaluation runs."""

    async def spend(self) -> None:
        return None


class GuardedLlm:
    def __init__(
        self,
        provider: LlmProvider,
        budget: UsageBudget,
        breaker: CircuitBreaker,
        timeout_s: float,
    ) -> None:
        self.provider = provider
        self._budget = budget
        self._breaker = breaker
        self._timeout_s = timeout_s

    async def complete_json(
        self, task: str, system: str, user: str, max_tokens: int
    ) -> LlmResponse:
        await self._budget.spend()
        for attempt in (1, 2):
            try:
                self._breaker.before_call()
            except CircuitOpenError as exc:
                raise LlmUnavailableError("The language model is temporarily unavailable") from exc
            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    self.provider.complete_json(system, user, max_tokens), self._timeout_s
                )
            except (TimeoutError, TransientLlmError) as exc:
                self._breaker.record_failure()
                logger.warning(
                    "llm_call_failed", task=task, attempt=attempt, error_type=type(exc).__name__
                )
                if attempt == 2:
                    raise LlmUnavailableError("The language model did not respond") from exc
                continue
            self._breaker.record_success()
            logger.info(
                "llm_call",
                task=task,
                provider=self.provider.name,
                model=response.model,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
            return response
        raise AssertionError("unreachable")
