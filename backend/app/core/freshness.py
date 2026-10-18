"""Classify how current each upstream data source is.

Freshness is computed when it is read, never stored, so a source that stops
updating degrades on its own even if nothing else runs. Thresholds come from
measured update rates (October 2026): realtime feeds update every 15-30 s;
the elevator feed carries no timestamp, so its age is the time since the last
successful fetch.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class SourceKind(StrEnum):
    ACCESSIBILITY_STATUS = "accessibility_status"
    REALTIME_SERVICE = "realtime_service"
    STATIC_SCHEDULE = "static_schedule"
    SERVICE_ALERTS = "service_alerts"


@dataclass(frozen=True)
class Thresholds:
    fresh: timedelta
    stale: timedelta


THRESHOLDS = {
    SourceKind.ACCESSIBILITY_STATUS: Thresholds(timedelta(minutes=10), timedelta(minutes=30)),
    SourceKind.REALTIME_SERVICE: Thresholds(timedelta(minutes=2), timedelta(minutes=10)),
    SourceKind.SERVICE_ALERTS: Thresholds(timedelta(minutes=5), timedelta(minutes=20)),
}
# Static timetables are "stale" when they stop covering service this soon.
STATIC_EXPIRY_WARNING = timedelta(days=7)


def classify(
    kind: SourceKind,
    observed_at: datetime | None,
    valid_until: datetime | None,
    now: datetime,
) -> Freshness:
    if kind is SourceKind.STATIC_SCHEDULE:
        if valid_until is None:
            return Freshness.UNKNOWN
        if valid_until <= now:
            return Freshness.EXPIRED
        return Freshness.STALE if valid_until - now < STATIC_EXPIRY_WARNING else Freshness.FRESH

    if observed_at is None:
        return Freshness.UNKNOWN
    age = now - observed_at
    limits = THRESHOLDS[kind]
    if age <= limits.fresh:
        return Freshness.FRESH
    return Freshness.STALE if age <= limits.stale else Freshness.EXPIRED
