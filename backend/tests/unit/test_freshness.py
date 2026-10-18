from datetime import UTC, datetime, timedelta

import pytest

from app.core.freshness import Freshness, SourceKind, classify

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(minutes=1), Freshness.FRESH),
        (timedelta(minutes=10), Freshness.FRESH),
        (timedelta(minutes=11), Freshness.STALE),
        (timedelta(minutes=31), Freshness.EXPIRED),
    ],
)
def test_accessibility_status_ages(age: timedelta, expected: Freshness) -> None:
    assert classify(SourceKind.ACCESSIBILITY_STATUS, NOW - age, None, NOW) is expected


def test_realtime_is_held_to_a_tighter_standard() -> None:
    three_minutes_ago = NOW - timedelta(minutes=3)
    assert classify(SourceKind.REALTIME_SERVICE, three_minutes_ago, None, NOW) is Freshness.STALE
    assert (
        classify(SourceKind.ACCESSIBILITY_STATUS, three_minutes_ago, None, NOW) is Freshness.FRESH
    )


def test_never_observed_is_unknown() -> None:
    assert classify(SourceKind.REALTIME_SERVICE, None, None, NOW) is Freshness.UNKNOWN


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [
        (timedelta(days=30), Freshness.FRESH),
        (timedelta(days=3), Freshness.STALE),
        (timedelta(seconds=-1), Freshness.EXPIRED),
    ],
)
def test_static_timetables_by_end_of_service(remaining: timedelta, expected: Freshness) -> None:
    assert classify(SourceKind.STATIC_SCHEDULE, NOW, NOW + remaining, NOW) is expected
