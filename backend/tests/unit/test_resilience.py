import pytest

from app.core.resilience import BreakerState, CircuitBreaker, CircuitOpenError


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker("test", failure_threshold=3, reset_timeout_s=10, clock=clock)


def test_stays_closed_below_threshold(breaker: CircuitBreaker) -> None:
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.before_call()


def test_success_resets_failure_count(breaker: CircuitBreaker) -> None:
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED


def test_opens_at_threshold_and_fails_fast(breaker: CircuitBreaker) -> None:
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_half_open_allows_a_single_trial(breaker: CircuitBreaker, clock: FakeClock) -> None:
    for _ in range(3):
        breaker.record_failure()
    clock.now = 10
    assert breaker.state is BreakerState.HALF_OPEN
    breaker.before_call()
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_successful_trial_closes(breaker: CircuitBreaker, clock: FakeClock) -> None:
    for _ in range(3):
        breaker.record_failure()
    clock.now = 10
    breaker.before_call()
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED


def test_failed_trial_reopens_for_full_period(breaker: CircuitBreaker, clock: FakeClock) -> None:
    for _ in range(3):
        breaker.record_failure()
    clock.now = 10
    breaker.before_call()
    breaker.record_failure()
    observed = []
    for now in (10, 19.9, 20):
        clock.now = now
        observed.append(breaker.state)
    assert observed == [BreakerState.OPEN, BreakerState.OPEN, BreakerState.HALF_OPEN]
