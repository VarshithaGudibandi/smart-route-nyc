"""Label routes that rely on our estimate of a same-platform change.

The graph build lets a change between lines that share a platform take one
minute, where MTA's timetable allows three (``otp/scripts/same_platform.py``,
which writes the station list read here). That is our estimate, not MTA's,
so every route that changes faster than MTA's minimum at one of those
stations says so.
"""

import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import structlog

from app.schemas.routes import Notice, Route

logger = structlog.get_logger(__name__)

SUBWAY_FEED = "MTASBWY"


class SamePlatformChanges:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._cached: tuple[int, dict[str, Any]] | None = None

    def _stations(self) -> dict[str, Any]:
        if self._path is None:
            return {}
        try:
            mtime = self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return {}
        if self._cached is None or self._cached[0] != mtime:
            try:
                stations = json.loads(self._path.read_text())["stations"]
            except (ValueError, KeyError) as exc:
                logger.warning("same_platform_file_invalid", error=type(exc).__name__)
                stations = {}
            self._cached = (mtime, stations)
        return self._cached[1]

    def label(self, route: Route) -> Route:
        stations = self._stations()
        if not stations:
            return route
        notes = list(route.notes)
        transit = [leg for leg in route.legs if leg.route_id]
        for into, out_of in pairwise(transit):
            station = out_of.origin.station_id or ""
            feed, _, stop_id = station.partition(":")
            entry = stations.get(stop_id) if feed == SUBWAY_FEED else None
            if entry is None or into.destination.station_id != station:
                continue
            gap = (out_of.start_time - into.end_time).total_seconds()
            mta_minimum = int(entry.get("mta_min_transfer_s", 0))
            if gap < mta_minimum:
                notes.append(
                    Notice(
                        code="SAME_PLATFORM_CHANGE",
                        message=(
                            f"Assumes a 1-minute change across the platform at {entry['name']}; "
                            f"MTA's timetable allows {round(mta_minimum / 60)} min."
                        ),
                    )
                )
        return route.model_copy(update={"notes": notes}) if notes != route.notes else route
