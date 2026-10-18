"""Scheduled service at a station: which lines run where, and how often.

Route suggestions need facts the itinerary alone does not carry: which
other lines leave the boarding station in the next hour, where each one
stops, and how frequent it is. All of it comes from OTP's timetable for the
graph it is serving, never from the model.

Station IDs here are the IDs used throughout the API: the GTFS parent
station when a platform has one (``MTASBWY:137``), otherwise the stop itself
(PATH and bus stops are often their own station). A pattern's stop sequence
is mapped onto those IDs, so "does this line reach that station" is a plain
lookup.

Answers depend only on the published schedule, so they are cached in Redis
per graph version, station and 15-minute window; a rebuilt graph retires
them. If Redis is down, OTP is queried directly.
"""

import statistics
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from app.cache.redis import ResilientRedis
from app.core.graph_version import GraphVersion
from app.integrations.otp.client import OtpClient
from app.services.cache_keys import NEW_YORK

logger = structlog.get_logger(__name__)

CACHE_KEY_VERSION = 1
WINDOW = timedelta(hours=1)
WINDOW_BUCKET = timedelta(minutes=15)
# Enough to count departures of a frequent line over the whole window.
DEPARTURES_PER_PATTERN = 40
CACHE_TTL_S = 6 * 3600


class PatternService(BaseModel):
    """One stopping pattern of one line, as seen from a station."""

    model_config = ConfigDict(frozen=True)

    pattern: str
    route_id: str
    route: str
    mode: str
    direction_id: int | None
    headsign: str | None
    # Station IDs in stop order, the whole pattern (not only after this station).
    stations: tuple[str, ...]
    # Scheduled departures from this station inside the window, ascending (epoch s).
    departures: tuple[int, ...]

    def reaches(self, origin: str, destination: str) -> bool:
        """True if a rider boarding at ``origin`` can stay on to ``destination``."""
        try:
            start = self.stations.index(origin)
        except ValueError:
            return False
        return destination in self.stations[start + 1 :]

    def stations_after(self, origin: str) -> tuple[str, ...]:
        try:
            return self.stations[self.stations.index(origin) + 1 :]
        except ValueError:
            return ()

    @property
    def headway_s(self) -> int | None:
        """Median gap between departures in the window; ``None`` below two trips."""
        if len(self.departures) < 2:
            return None
        gaps = (b - a for a, b in pairwise(self.departures))
        return int(statistics.median(gaps))


class StationService(BaseModel):
    model_config = ConfigDict(frozen=True)

    station_id: str
    name: str
    window_start: datetime
    window_s: int
    patterns: tuple[PatternService, ...]
    # Names of every station the patterns visit, for building readable facts.
    station_names: dict[str, str]

    def patterns_reaching(self, destination: str) -> tuple[PatternService, ...]:
        return tuple(p for p in self.patterns if p.reaches(self.station_id, destination))


def window_start(moment: datetime) -> datetime:
    """Floor to the 15-minute window in New York time, so nearby searches share it."""
    local = moment.astimezone(NEW_YORK)
    minutes = int(WINDOW_BUCKET.total_seconds() // 60)
    return local.replace(minute=local.minute - local.minute % minutes, second=0, microsecond=0)


def parse_station(raw: dict[str, Any], start: datetime, window_s: int) -> StationService:
    station_id: str = raw["gtfsId"]
    first = int(start.timestamp())
    names: dict[str, str] = {station_id: raw["name"]}
    patterns = []
    for entry in raw["stoptimesForPatterns"]:
        pattern = entry["pattern"]
        route = pattern["route"]
        stations = []
        for stop in pattern["stops"]:
            parent = stop.get("parentStation")
            sid = parent["gtfsId"] if parent else stop["gtfsId"]
            names.setdefault(sid, parent["name"] if parent else stop["name"])
            stations.append(sid)
        # OTP's range is inclusive at the end and unordered; normalise both.
        departures = tuple(
            sorted(
                t
                for t in (
                    int(st["serviceDay"]) + int(st["scheduledDeparture"])
                    for st in entry["stoptimes"]
                )
                if first <= t < first + window_s
            )
        )
        if not departures:
            continue
        patterns.append(
            PatternService(
                pattern=pattern["code"],
                route_id=route["gtfsId"],
                route=route.get("shortName") or route["gtfsId"],
                mode=route.get("mode") or "TRANSIT",
                direction_id=pattern.get("directionId"),
                headsign=pattern.get("headsign"),
                stations=tuple(stations),
                departures=departures,
            )
        )
    return StationService(
        station_id=station_id,
        name=raw["name"],
        window_start=start,
        window_s=window_s,
        patterns=tuple(patterns),
        station_names=names,
    )


class TransitKnowledge:
    def __init__(
        self,
        otp: OtpClient,
        redis: ResilientRedis,
        graph_version: GraphVersion,
    ) -> None:
        self._otp = otp
        self._redis = redis
        self._graph_version = graph_version

    async def station(self, station_id: str, around: datetime) -> StationService | None:
        """Scheduled service at ``station_id`` for the hour from ``around``'s window."""
        start = window_start(around)
        window_s = int(WINDOW.total_seconds())
        key = self._key(station_id, start)
        cached = await self._redis.get(key)
        if cached is not None:
            try:
                return StationService.model_validate_json(cached)
            except ValidationError:
                logger.warning("transit_knowledge_cache_invalid", station_id=station_id)

        raw = await self._otp.station_departures(
            station_id, start, window_s, DEPARTURES_PER_PATTERN
        )
        if raw is None:
            return None
        service = parse_station(raw, start, window_s)
        await self._redis.set(key, service.model_dump_json().encode(), ttl_s=CACHE_TTL_S)
        return service

    def _key(self, station_id: str, start: datetime) -> str:
        # Station IDs and times are public timetable facts, so no HMAC is needed.
        return (
            f"srn:v{CACHE_KEY_VERSION}:station:{self._graph_version.current()}:"
            f"{station_id}:{start.strftime('%Y%m%dT%H%M')}"
        )
