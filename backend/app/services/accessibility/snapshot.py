"""In-memory view of elevator inventory and status, refreshed on change.

Assessment runs on every search response, so it must not hit PostgreSQL each
time. The snapshot is reloaded when the ingestion worker bumps the
accessibility version in Redis (an elevator changed status), and at least
every ``MAX_AGE`` so freshness is re-evaluated even when nothing changes.
If Redis is unavailable, only the age rule applies.
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.cache.redis import ResilientRedis
from app.db.models import (
    AccessibilityEquipment,
    AccessibilityEquipmentStop,
    EquipmentStatus,
    SourceFreshness,
)

logger = structlog.get_logger(__name__)

ACCESSIBILITY_VERSION_KEY = "srn:v1:ver:accessibility"
ELEVATOR_SOURCE = "mta_elevators"
MAX_AGE_S = 30.0


@dataclass(frozen=True)
class Elevator:
    equipment_id: str
    ada_accessible: bool
    in_service: bool
    serving: str | None
    reason: str | None
    estimated_return_at: datetime | None


@dataclass(frozen=True)
class AccessibilitySnapshot:
    # Active elevators by GTFS stop ID (e.g. "120"); escalators are not
    # step-free access and are left out.
    elevators_by_stop: dict[str, list[Elevator]] = field(default_factory=dict)
    observed_at: datetime | None = None

    @property
    def loaded(self) -> bool:
        return self.observed_at is not None


class SnapshotProvider:
    def __init__(self, engine: AsyncEngine, redis: ResilientRedis) -> None:
        self._engine = engine
        self._redis = redis
        self._snapshot = AccessibilitySnapshot()
        self._version: bytes | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    async def current(self) -> AccessibilitySnapshot:
        version = await self._redis.get(ACCESSIBILITY_VERSION_KEY)
        if not self._needs_reload(version):
            return self._snapshot
        async with self._lock:
            if self._needs_reload(version):
                try:
                    self._snapshot = await self._load()
                    self._version = version
                    self._loaded_at = time.monotonic()
                except SQLAlchemyError as exc:
                    # Keep serving the previous snapshot; its age keeps growing,
                    # so freshness checks downgrade it to stale on their own.
                    logger.warning("accessibility_snapshot_load_failed", error=type(exc).__name__)
        return self._snapshot

    def _needs_reload(self, version: bytes | None) -> bool:
        return (
            not self._snapshot.loaded
            or version != self._version
            or time.monotonic() - self._loaded_at > MAX_AGE_S
        )

    async def _load(self) -> AccessibilitySnapshot:
        async with AsyncSession(self._engine) as session:
            rows = await session.execute(
                select(
                    AccessibilityEquipmentStop.gtfs_stop_id,
                    AccessibilityEquipment.equipment_external_id,
                    AccessibilityEquipment.ada_accessible,
                    AccessibilityEquipment.serving,
                    EquipmentStatus.status,
                    EquipmentStatus.reason,
                    EquipmentStatus.estimated_return_at,
                )
                .join(
                    AccessibilityEquipment,
                    (AccessibilityEquipment.operator == AccessibilityEquipmentStop.operator)
                    & (
                        AccessibilityEquipment.equipment_external_id
                        == AccessibilityEquipmentStop.equipment_external_id
                    ),
                )
                .outerjoin(
                    EquipmentStatus,
                    (EquipmentStatus.operator == AccessibilityEquipment.operator)
                    & (
                        EquipmentStatus.equipment_external_id
                        == AccessibilityEquipment.equipment_external_id
                    ),
                )
                .where(
                    AccessibilityEquipment.equipment_type == "EL",
                    AccessibilityEquipment.is_active.is_(True),
                )
            )
            freshness = await session.get(SourceFreshness, ELEVATOR_SOURCE)

        by_stop: dict[str, list[Elevator]] = defaultdict(list)
        for stop_id, equipment_id, ada, serving, status, reason, eta in rows.all():
            by_stop[stop_id].append(
                Elevator(
                    equipment_id=equipment_id,
                    ada_accessible=ada,
                    # No status row yet means never observed: not proven working.
                    in_service=status == "in_service",
                    serving=serving,
                    reason=reason,
                    estimated_return_at=eta,
                )
            )
        return AccessibilitySnapshot(
            elevators_by_stop=dict(by_stop),
            observed_at=freshness.observed_at if freshness else None,
        )
