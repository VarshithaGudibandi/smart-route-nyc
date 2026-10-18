"""MTA elevator and escalator inventory and current outages.

Two feeds are combined in one run:

* ``nyct_ene_equipments.json``: every elevator and escalator, with the GTFS
  stop IDs it serves (``elevatorsgtfsstopid``, e.g. ``"A31/L01"`` for a
  station complex).
* ``nyct_ene.json``: outages. Despite its name it also lists *scheduled future*
  outages (``isupcomingoutage == "Y"``). Those are ignored here: treating them
  as current would report working elevators as broken.

The outage feed carries no timestamp, so a status is "observed" when it is
fetched. Writes are idempotent: rerunning on the same data changes nothing,
and an older observation never overwrites a newer one.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.redis import ResilientRedis
from app.core.freshness import SourceKind
from app.db.models import (
    AccessibilityEquipment,
    AccessibilityEquipmentStop,
    AccessibilityEvent,
    EquipmentStatus,
)
from app.ingestion.http import FetchError, fetch_bytes
from app.ingestion.runs import RunContext, RunOutcome

logger = structlog.get_logger(__name__)

OPERATOR = "MTA"
NEW_YORK = ZoneInfo("America/New_York")
IN_SERVICE = "in_service"
OUT_OF_SERVICE = "out_of_service"
# Bumped whenever any equipment status changes, so results that depend on
# accessibility can be invalidated without scanning caches.
ACCESSIBILITY_VERSION_KEY = "srn:v1:ver:accessibility"


@dataclass(frozen=True)
class Equipment:
    equipment_id: str
    equipment_type: str
    station_name: str
    station_complex_id: str | None
    gtfs_stop_ids: tuple[str, ...]
    serving: str | None
    lines_served: str | None
    ada_accessible: bool
    is_active: bool
    has_redundant: bool
    alternative_route: str | None


@dataclass(frozen=True)
class Outage:
    equipment_id: str
    reason: str | None
    started_at: datetime | None
    estimated_return_at: datetime | None


def parse_equipment(raw: list[dict[str, Any]]) -> list[Equipment]:
    return [
        Equipment(
            equipment_id=item["equipmentno"],
            equipment_type=item["equipmenttype"],
            station_name=item["station"],
            station_complex_id=item.get("stationcomplexid") or None,
            gtfs_stop_ids=_stop_ids(item.get("elevatorsgtfsstopid")),
            serving=item.get("serving") or None,
            lines_served=item.get("linesservedbyelevator") or None,
            ada_accessible=item.get("ADA") == "Y",
            is_active=item.get("isactive") == "Y",
            has_redundant=bool(item.get("redundant")),
            alternative_route=item.get("alternativeroute") or None,
        )
        for item in raw
    ]


def parse_current_outages(raw: list[dict[str, Any]]) -> dict[str, Outage]:
    return {
        item["equipment"]: Outage(
            equipment_id=item["equipment"],
            reason=item.get("reason") or None,
            started_at=_mta_time(item.get("outagedate")),
            estimated_return_at=_mta_time(item.get("estimatedreturntoservice")),
        )
        for item in raw
        if item.get("isupcomingoutage") == "N"
    }


def _stop_ids(value: str | None) -> tuple[str, ...]:
    """``"132/D19/L02"`` -> ``("132", "D19", "L02")``."""
    if not value:
        return ()
    return tuple(dict.fromkeys(part.strip() for part in value.split("/") if part.strip()))


def _mta_time(value: str | None) -> datetime | None:
    """MTA publishes local times like ``"10/30/2026 10:00:00 PM"``."""
    if not value:
        return None
    try:
        local = datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p")
    except ValueError:
        return None
    return local.replace(tzinfo=NEW_YORK)


class MtaElevatorSource:
    provider = "mta"
    source_key = "mta_elevators"
    kind = SourceKind.ACCESSIBILITY_STATUS

    def __init__(
        self,
        http: httpx.AsyncClient,
        sessions: async_sessionmaker[AsyncSession],
        redis: ResilientRedis,
        base_url: str,
    ) -> None:
        self._http = http
        self._sessions = sessions
        self._redis = redis
        self._equipment_url = f"{base_url}/nyct%2Fnyct_ene_equipments.json"
        self._outages_url = f"{base_url}/nyct%2Fnyct_ene.json"

    async def run(self, ctx: RunContext) -> RunOutcome:
        equipment_raw = await fetch_bytes(self._http, self._equipment_url)
        outages_raw = await fetch_bytes(self._http, self._outages_url)
        checksum = hashlib.sha256(equipment_raw + b"\n" + outages_raw).hexdigest()
        if checksum == ctx.previous_checksum:
            return RunOutcome(checksum=checksum, unchanged=True)

        equipment = parse_equipment(json.loads(equipment_raw))
        if not equipment:
            # An empty inventory means a broken upstream response, not that every
            # elevator was removed; applying it would wipe all accessibility data.
            raise FetchError("equipment feed returned no equipment", "empty_feed")
        outages = parse_current_outages(json.loads(outages_raw))
        now = datetime.now(UTC)
        version = checksum[:16]

        async with self._sessions.begin() as session:
            await self._store_inventory(session, equipment, now, version)
            changed = await self._store_statuses(session, equipment, outages, now, version)

        if changed:
            await self._redis.increment(ACCESSIBILITY_VERSION_KEY)
        unknown = set(outages) - {e.equipment_id for e in equipment}
        if unknown:
            logger.warning("outages_for_unknown_equipment", count=len(unknown))
        return RunOutcome(
            checksum=checksum,
            observed_at=now,
            stats={
                "equipment": len(equipment),
                "out_of_service": len(outages),
                "status_changes": changed,
            },
        )

    async def _store_inventory(
        self, session: AsyncSession, equipment: list[Equipment], now: datetime, version: str
    ) -> None:
        rows = [
            {
                "operator": OPERATOR,
                "equipment_external_id": e.equipment_id,
                "equipment_type": e.equipment_type,
                "station_name": e.station_name,
                "station_complex_id": e.station_complex_id,
                "serving": e.serving,
                "lines_served": e.lines_served,
                "ada_accessible": e.ada_accessible,
                "is_active": e.is_active,
                "has_redundant": e.has_redundant,
                "alternative_route": e.alternative_route,
                "fetched_at": now,
                "source_version": version,
            }
            for e in equipment
        ]
        if rows:
            stmt = insert(AccessibilityEquipment).values(rows)
            key_columns = {"operator", "equipment_external_id"}
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=list(key_columns),
                    set_={k: stmt.excluded[k] for k in rows[0] if k not in key_columns},
                )
            )
        # Equipment the operator no longer lists is removed (stop links cascade).
        current_ids = [e.equipment_id for e in equipment]
        await session.execute(
            delete(AccessibilityEquipment).where(
                AccessibilityEquipment.operator == OPERATOR,
                AccessibilityEquipment.equipment_external_id.not_in(current_ids),
            )
        )
        # The stop mapping is small; replacing it keeps it exactly in sync.
        await session.execute(
            delete(AccessibilityEquipmentStop).where(
                AccessibilityEquipmentStop.operator == OPERATOR
            )
        )
        stops = [
            {"operator": OPERATOR, "equipment_external_id": e.equipment_id, "gtfs_stop_id": stop}
            for e in equipment
            for stop in e.gtfs_stop_ids
        ]
        if stops:
            await session.execute(insert(AccessibilityEquipmentStop).values(stops))

    async def _store_statuses(
        self,
        session: AsyncSession,
        equipment: list[Equipment],
        outages: dict[str, Outage],
        now: datetime,
        version: str,
    ) -> int:
        result = await session.execute(
            select(EquipmentStatus.equipment_external_id, EquipmentStatus.status).where(
                EquipmentStatus.operator == OPERATOR
            )
        )
        previous = dict(result.all())
        rows: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for e in equipment:
            outage = outages.get(e.equipment_id)
            status = OUT_OF_SERVICE if outage else IN_SERVICE
            rows.append(
                {
                    "operator": OPERATOR,
                    "equipment_external_id": e.equipment_id,
                    "station_external_id": "/".join(e.gtfs_stop_ids) or None,
                    "equipment_type": e.equipment_type,
                    "status": status,
                    "reason": outage.reason if outage else None,
                    "outage_started_at": outage.started_at if outage else None,
                    "estimated_return_at": outage.estimated_return_at if outage else None,
                    "observed_at": now,
                    "fetched_at": now,
                    "source_version": version,
                }
            )
            if previous.get(e.equipment_id) != status:
                events.append(
                    {
                        "operator": OPERATOR,
                        "station_external_id": "/".join(e.gtfs_stop_ids) or None,
                        "equipment_external_id": e.equipment_id,
                        "event_type": "status_change",
                        "status": status,
                        "observed_at": now,
                        "fetched_at": now,
                        "raw_source_ref": f"mta_ene:{version}",
                    }
                )

        if rows:
            stmt = insert(EquipmentStatus).values(rows)
            key_columns = {"operator", "equipment_external_id"}
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=list(key_columns),
                    set_={k: stmt.excluded[k] for k in rows[0] if k not in key_columns},
                    # Never let an older observation overwrite a newer one.
                    where=(EquipmentStatus.observed_at.is_(None))
                    | (stmt.excluded.observed_at >= EquipmentStatus.observed_at),
                )
            )
        if events:
            await session.execute(
                insert(AccessibilityEvent).values(events).on_conflict_do_nothing()
            )
        return len(events)
