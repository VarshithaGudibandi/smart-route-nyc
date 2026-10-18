"""Current MTA subway alerts, as an in-memory view for the API.

Reloaded when the ingestion worker bumps the alerts version in Redis, and
at least every ``MAX_AGE_S``. The feed's IDs carry no feed prefix (route
``"2"``, stop ``"120"``), while the routing graph's do (``MTASBWY:2``); the
prefix is added here so callers compare like with like.

Planned work is already in the supplemented subway timetable OTP routes on,
so it is informational. Unplanned suspensions are not, which is why they
matter for suggestions: a timetable can show a line that is not running.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.cache.redis import ResilientRedis
from app.db.models import ServiceAlert
from app.ingestion.sources.mta_alerts import ALERTS_VERSION_KEY

logger = structlog.get_logger(__name__)

SUBWAY_FEED = "MTASBWY"
MAX_AGE_S = 60.0
PLANNED_PREFIX = "Planned"


@dataclass(frozen=True)
class ActiveAlert:
    alert_id: str
    alert_type: str | None
    header: str
    periods: tuple[tuple[int, int | None], ...]
    route_ids: frozenset[str]
    station_ids: frozenset[str]

    def active_at(self, moment: datetime) -> bool:
        at = moment.timestamp()
        return any(start <= at and (end is None or at < end) for start, end in self.periods)

    @property
    def suspends_service(self) -> bool:
        """Unplanned "Suspended" or "Part Suspended": trains are not running and
        the timetable does not know it. Matched by word, since MTA documents
        these as service statuses rather than as a fixed list of feed values."""
        kind = self.alert_type or ""
        return "Suspended" in kind and not kind.startswith(PLANNED_PREFIX)


@dataclass(frozen=True)
class AlertSnapshot:
    alerts: tuple[ActiveAlert, ...] = ()
    loaded: bool = False
    by_route: dict[str, tuple[ActiveAlert, ...]] = field(default_factory=dict)

    @classmethod
    def build(cls, alerts: list[ActiveAlert]) -> "AlertSnapshot":
        by_route: dict[str, list[ActiveAlert]] = {}
        for alert in alerts:
            for route_id in alert.route_ids:
                by_route.setdefault(route_id, []).append(alert)
        return cls(
            alerts=tuple(alerts),
            loaded=True,
            by_route={k: tuple(v) for k, v in by_route.items()},
        )

    def for_route(self, route_id: str, at: datetime) -> list[ActiveAlert]:
        return [a for a in self.by_route.get(route_id, ()) if a.active_at(at)]


def to_active_alert(row: ServiceAlert) -> ActiveAlert:
    entities = row.informed_entities
    return ActiveAlert(
        alert_id=row.alert_external_id,
        alert_type=row.alert_type,
        header=row.header_text,
        periods=tuple((int(p[0] or 0), int(p[1]) if p[1] else None) for p in row.active_periods),
        route_ids=frozenset(f"{SUBWAY_FEED}:{e['route_id']}" for e in entities if "route_id" in e),
        station_ids=frozenset(f"{SUBWAY_FEED}:{e['stop_id']}" for e in entities if "stop_id" in e),
    )


class AlertsProvider:
    def __init__(self, engine: AsyncEngine, redis: ResilientRedis) -> None:
        self._engine = engine
        self._redis = redis
        self._snapshot = AlertSnapshot()
        self._version: bytes | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    async def current(self) -> AlertSnapshot:
        version = await self._redis.get(ALERTS_VERSION_KEY)
        if not self._needs_reload(version):
            return self._snapshot
        async with self._lock:
            if self._needs_reload(version):
                try:
                    async with AsyncSession(self._engine) as session:
                        rows = await session.scalars(
                            select(ServiceAlert).where(ServiceAlert.operator == "MTA")
                        )
                        self._snapshot = AlertSnapshot.build([to_active_alert(r) for r in rows])
                    self._version = version
                    self._loaded_at = time.monotonic()
                except SQLAlchemyError as exc:
                    logger.warning("alert_snapshot_load_failed", error=type(exc).__name__)
        return self._snapshot

    def _needs_reload(self, version: bytes | None) -> bool:
        return (
            not self._snapshot.loaded
            or version != self._version
            or time.monotonic() - self._loaded_at > MAX_AGE_S
        )
