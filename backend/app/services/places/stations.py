"""Search the stations in the routing graph by name.

Built from OTP itself, so a station that can be found can also be routed
to, and the list changes with the graph: subway stations come with the
lines at their platforms; PATH stops are grouped by name, since PATH's GTFS
gives each platform its own stop and no parent station.

Matching is on word prefixes after normalizing common abbreviations, so
"herald sq", "34 st herald" and "Herald Square" find the same station.
"""

import asyncio
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.core.graph_version import GraphVersion
from app.integrations.otp.client import OtpClient
from app.schemas.places import PlaceResult

ABBREVIATIONS = {
    "street": "st",
    "avenue": "av",
    "ave": "av",
    "boulevard": "blvd",
    "square": "sq",
    "road": "rd",
    "parkway": "pkwy",
    "place": "pl",
    "center": "ctr",
    "centre": "ctr",
    "heights": "hts",
    "junction": "jct",
}
# Names riders use that are not in the station name.
ALIASES = {"World Trade Center": ("wtc",)}
COMPLEX_RADIUS_M = 250
_ORDINAL = re.compile(r"^(\d+)(st|nd|rd|th)$")
PATH_LINES = {
    "NWK_WTC": "Newark\u2013WTC",
    "HOB_WTC": "Hoboken\u2013WTC",
    "JSQ_33": "Journal Sq\u201333 St",
    "HOB_33": "Hoboken\u201333 St",
    "JSQ_HOB_33": "Journal Sq\u201333 St via Hoboken",
}


def tokens(text: str) -> list[str]:
    out = []
    # Split before folding to ASCII, which would delete en dashes and join words.
    for part in re.split(r"[\s\-/,()\u2013\u2014]+", text):
        word = unicodedata.normalize("NFKD", part).encode("ascii", "ignore").decode().lower()
        word = word.strip(".'")
        if not word:
            continue
        word = ABBREVIATIONS.get(word, word)
        if match := _ORDINAL.match(word):
            word = match.group(1)
        out.append(word)
    return out


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    detail: str
    lat: float
    lon: float
    words: tuple[str, ...]

    def result(self) -> PlaceResult:
        return PlaceResult(
            id=self.id,
            name=self.name,
            detail=self.detail,
            kind="station",
            lat=self.lat,
            lon=self.lon,
            source="transit",
        )


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(lat)
    return math.hypot(dx, dy)


def _line_order(name: str) -> tuple[bool, str]:
    return (not name.isdigit(), name.zfill(3))


def build_index(data: dict[str, Any]) -> list[Station]:
    # A station complex (Times Sq-42 St) is several GTFS stations with one
    # name a short walk apart: one result, all its lines. Same-named
    # stations further apart (the 96 Sts on different avenues) stay separate.
    complexes: list[dict[str, Any]] = []
    for station in data["stations"]:
        lines = {
            r["shortName"]
            for stop in station.get("stops") or []
            for r in stop.get("routes") or []
            if r.get("shortName") and not r["shortName"].endswith("X")
        }
        point = (station["lat"], station["lon"])
        group = next(
            (
                c
                for c in complexes
                if c["name"] == station["name"]
                and _distance_m(c["points"][0], point) <= COMPLEX_RADIUS_M
            ),
            None,
        )
        if group is None:
            complexes.append(
                {
                    "id": station["gtfsId"],
                    "name": station["name"],
                    "points": [point],
                    "lines": lines,
                }
            )
        else:
            group["points"].append(point)
            group["lines"] |= lines

    stations = []
    for c in complexes:
        lats, lons = zip(*c["points"], strict=True)
        ordered = sorted(c["lines"], key=_line_order)
        stations.append(
            Station(
                id=c["id"],
                name=c["name"],
                detail="Subway · " + " ".join(ordered) if ordered else "Subway",
                lat=round(sum(lats) / len(lats), 6),
                lon=round(sum(lons) / len(lons), 6),
                words=tuple(tokens(c["name"])),
            )
        )

    path: dict[str, dict[str, Any]] = {}
    for route in data["routes"]:
        line = PATH_LINES.get(route.get("longName") or "", route.get("longName") or "PATH")
        for pattern in route["patterns"]:
            for stop in pattern["stops"]:
                entry = path.setdefault(
                    stop["name"], {"id": stop["gtfsId"], "coords": [], "lines": set()}
                )
                entry["coords"].append((stop["lat"], stop["lon"]))
                entry["lines"].add(line)
    for name, entry in path.items():
        lats, lons = zip(*entry["coords"], strict=True)
        stations.append(
            Station(
                id=entry["id"],
                name=name,
                detail="PATH · " + ", ".join(sorted(entry["lines"])),
                lat=round(sum(lats) / len(lats), 6),
                lon=round(sum(lons) / len(lons), 6),
                words=tuple(tokens(name)) + ALIASES.get(name, ()),
            )
        )
    return stations


def search(stations: list[Station], query: str, limit: int) -> list[Station]:
    wanted = tokens(query)
    if not wanted:
        return []
    scored = []
    for station in stations:
        if all(any(word.startswith(w) for word in station.words) for w in wanted):
            starts = station.words[0].startswith(wanted[0])
            exact = sum(w in station.words for w in wanted)
            scored.append(((not starts, -exact, len(station.name), station.name), station))
    return [station for _, station in sorted(scored, key=lambda item: item[0])[:limit]]


class StationIndex:
    """The graph's stations, reloaded when the graph version changes."""

    def __init__(self, otp: OtpClient, graph_version: GraphVersion) -> None:
        self._otp = otp
        self._graph_version = graph_version
        self._loaded: tuple[str, list[Station]] | None = None
        self._lock = asyncio.Lock()

    async def search(self, query: str, limit: int) -> list[PlaceResult]:
        return [s.result() for s in search(await self._stations(), query, limit)]

    async def _stations(self) -> list[Station]:
        version = self._graph_version.current()
        if self._loaded is not None and self._loaded[0] == version:
            return self._loaded[1]
        async with self._lock:
            if self._loaded is None or self._loaded[0] != version:
                self._loaded = (version, build_index(await self._otp.transit_places()))
        return self._loaded[1]
