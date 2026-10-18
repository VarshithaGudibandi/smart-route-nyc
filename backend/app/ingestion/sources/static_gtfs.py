"""Expiry monitoring for the static timetables in the routing graph.

Reads the manifest written by ``otp/scripts/fetch_inputs.py`` and records,
per feed, the last day of published service. Timetables expire every few
months (the subway feed covers about five), so an approaching end date must
be visible well before routing silently stops finding trips.
"""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.freshness import SourceKind
from app.db.models import SourceFreshness
from app.ingestion.http import FetchError
from app.ingestion.runs import RunContext, RunOutcome

NEW_YORK = ZoneInfo("America/New_York")


class StaticGtfsMonitor:
    provider = "otp"
    source_key = "gtfs_static"
    # Writes one freshness row per feed itself, so it records none of its own.
    kind = None

    def __init__(self, sessions: async_sessionmaker[AsyncSession], manifest: Path | None) -> None:
        self._sessions = sessions
        self._manifest = manifest

    async def run(self, ctx: RunContext) -> RunOutcome:
        if self._manifest is None or not self._manifest.exists():
            raise FetchError("GTFS manifest not found", "manifest_missing")
        raw = self._manifest.read_bytes()
        manifest = json.loads(raw)
        fetched_at = datetime.fromisoformat(manifest["fetched_at"])

        feeds = [s for s in manifest["sources"] if s["ok"] and s.get("service_end")]
        async with self._sessions.begin() as session:
            for feed in feeds:
                # GTFS service dates are local; service runs through the end of that day.
                last_day = datetime.strptime(feed["service_end"], "%Y%m%d").date()
                valid_until = datetime.combine(last_day + timedelta(days=1), time(), NEW_YORK)
                values = {
                    "source_key": f"gtfs_static:{feed['file'].removesuffix('.zip')}",
                    "kind": SourceKind.STATIC_SCHEDULE.value,
                    "source_version": (feed.get("sha256") or "")[:16] or None,
                    "observed_at": fetched_at,
                    "fetched_at": datetime.now(UTC),
                    "current_status": "ok",
                    "valid_until": valid_until,
                }
                stmt = insert(SourceFreshness).values(**values)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[SourceFreshness.source_key],
                        set_={k: stmt.excluded[k] for k in values if k != "source_key"},
                    )
                )
        return RunOutcome(
            checksum=hashlib.sha256(raw).hexdigest(),
            observed_at=fetched_at,
            stats={"feeds": len(feeds)},
        )
