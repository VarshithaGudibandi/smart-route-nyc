"""Circuit breaker for calls to external dependencies.

After ``failure_threshold`` consecutive failures the breaker opens and calls
fail fast without touching the dependency. After ``reset_timeout_s`` one trial
call is let through (half-open): success closes the breaker, failure reopens it.
"""

import time
from collections.abc import Callable
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"circuit '{name}' is open")
        self.name = name


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int,
        reset_timeout_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._clock() - self._opened_at >= self.reset_timeout_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def before_call(self) -> None:
        """Raise ``CircuitOpenError`` if the call must not be attempted."""
        state = self.state
        if state is BreakerState.OPEN:
            raise CircuitOpenError(self.name)
        if state is BreakerState.HALF_OPEN:
            # Only one trial call at a time while half-open.
            if self._trial_in_flight:
                raise CircuitOpenError(self.name)
            self._trial_in_flight = True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._trial_in_flight = False

    def record_failure(self) -> None:
        self._trial_in_flight = False
        if self._opened_at is not None:
            # A failed half-open trial reopens the breaker for a full period.
            self._opened_at = self._clock()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock()
