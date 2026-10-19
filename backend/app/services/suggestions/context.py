"""Facts about the transfers a journey's subway segment could make.

The investigated segment is the run of consecutive subway legs in the
fastest baseline route, from where the rider boards the subway to where
they leave it. Everything else in the journey (PATH, buses, walking) is
left to OTP when a proposal is verified.

The facts are a small map of the corridor ahead:

* interchanges: the stations the rider passes before the alighting station
  where more than one line leaves toward the destination, such as an
  express stop on a local line;
* rides: for every pair of those stations (plus the boarding and alighting
  stations), the lines that run from one to the other, with stop counts
  and scheduled headways.

A plan is a path through that map: board, change at one or more
interchanges, alight. Local, express, back to local (1 to the 2/3 at
Chambers St, back to the 1 at 96 St) is a path with two changes. Stop
patterns and headways come from the trips that run in the hour of the
journey, so a line that runs local at night shows local stop counts then.
Every fact comes from the routing graph's timetable; IDs in the facts are
the only IDs a proposal may use.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.schemas.routes import Route
from app.services.transit.alerts import AlertSnapshot
from app.services.transit.knowledge import PatternService, StationService

SUBWAY_PREFIX = "MTASBWY:"
# Station lookups are cached per graph and 15 minutes, so only the first
# investigation of a corridor pays for them.
MAX_CANDIDATE_STATIONS = 40
CONCURRENT_LOOKUPS = 4


class StationLookup(Protocol):
    """What facts need from ``TransitKnowledge``."""

    async def station(self, station_id: str, around: datetime) -> StationService | None: ...


@dataclass(frozen=True)
class SubwaySegment:
    board_station: str
    alight_station: str
    board_time: datetime
    route_ids: tuple[str, ...]
    transfer_stations: tuple[str, ...]
    # Non-subway lines elsewhere in the journey; verification keeps them allowed.
    other_route_ids: tuple[str, ...]


@dataclass(frozen=True)
class LineOption:
    route_id: str
    route: str
    stops: int
    headway_s: int | None
    alerts: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransferFacts:
    segment: SubwaySegment
    board_name: str
    alight_name: str
    direct: tuple[LineOption, ...]
    # Stations where a change is possible, nearest first.
    interchanges: tuple[str, ...]
    names: dict[str, str]
    # Lines from one station of the map to a later one.
    rides: dict[tuple[str, str], tuple[LineOption, ...]]

    @property
    def stations(self) -> tuple[str, ...]:
        """Boarding station, interchanges, alighting station."""
        return (self.segment.board_station, *self.interchanges, self.segment.alight_station)

    def lines(self, origin: str, destination: str) -> tuple[LineOption, ...]:
        return self.rides.get((origin, destination), ())

    def name(self, station_id: str) -> str:
        return self.names.get(station_id, station_id)


def subway_segment(route: Route) -> SubwaySegment | None:
    """The consecutive subway legs of ``route``, or None if there are none.

    A journey that leaves the subway and re-enters it (subway, bus, subway)
    has no single segment and is not investigated.
    """
    transit = [leg for leg in route.legs if leg.route_id]
    subway = [i for i, leg in enumerate(transit) if (leg.route_id or "").startswith(SUBWAY_PREFIX)]
    if not subway or subway != list(range(subway[0], subway[-1] + 1)):
        return None
    legs = [transit[i] for i in subway]
    board, alight = legs[0].origin.station_id, legs[-1].destination.station_id
    if board is None or alight is None:
        return None
    return SubwaySegment(
        board_station=board,
        alight_station=alight,
        board_time=legs[0].start_time,
        route_ids=tuple(leg.route_id for leg in legs if leg.route_id),
        transfer_stations=tuple(
            leg.destination.station_id for leg in legs[:-1] if leg.destination.station_id
        ),
        other_route_ids=tuple(
            dict.fromkeys(
                leg.route_id
                for i, leg in enumerate(transit)
                if i not in subway and leg.route_id is not None
            )
        ),
    )


def _stops_between(
    pattern: PatternService, origin: str, destination: str, behind: frozenset[str] = frozenset()
) -> int | None:
    """Stops from origin to destination, or None if the pattern does not go
    there, or if on the way it runs back over ``behind`` (stations the rider
    has already passed), which means it is heading the wrong way."""
    try:
        start = pattern.stations.index(origin)
        end = pattern.stations.index(destination, start + 1)
    except ValueError:
        return None
    if behind & set(pattern.stations[start + 1 : end + 1]):
        return None
    return end - start


def _lines(
    service: StationService,
    destination: str,
    alerts: AlertSnapshot,
    at: datetime,
    behind: frozenset[str] = frozenset(),
) -> tuple[LineOption, ...]:
    """Lines from ``service``'s station to ``destination``: fewest stops and the
    shortest headway among that line's patterns."""
    best: dict[str, LineOption] = {}
    for pattern in service.patterns:
        stops = _stops_between(pattern, service.station_id, destination, behind)
        if stops is None:
            continue
        current = best.get(pattern.route_id)
        headways = [h for h in (pattern.headway_s, current.headway_s if current else None) if h]
        best[pattern.route_id] = LineOption(
            route_id=pattern.route_id,
            route=pattern.route,
            stops=min(stops, current.stops) if current else stops,
            headway_s=min(headways) if headways else None,
            alerts=tuple(a.header for a in alerts.for_route(pattern.route_id, at)),
        )
    return tuple(sorted(best.values(), key=lambda line: (line.stops, line.route)))


def candidate_stations(board: StationService, segment: SubwaySegment) -> list[str]:
    """Stations the rider passes before the alighting station, nearest first.

    "Forward" patterns are those from the boarding station that later reach
    the alighting station or a station where the baseline route transfers;
    a pattern heading the other way reaches neither.
    """
    distance: dict[str, int] = {}
    for pattern in _forward(board, segment):
        after = pattern.stations_after(board.station_id)
        if segment.alight_station in after:
            after = after[: after.index(segment.alight_station)]
        for offset, station in enumerate(after, start=1):
            distance[station] = min(distance.get(station, offset), offset)
    distance.pop(board.station_id, None)
    return sorted(distance, key=lambda s: (distance[s], s))[:MAX_CANDIDATE_STATIONS]


def _forward(board: StationService, segment: SubwaySegment) -> list[PatternService]:
    targets = {segment.alight_station, *segment.transfer_stations}
    return [p for p in board.patterns if targets & set(p.stations_after(board.station_id))]


def _behind(board: StationService, segment: SubwaySegment, station_id: str) -> frozenset[str]:
    """The boarding station and every station before ``station_id`` on the
    forward patterns that reach it."""
    passed = {board.station_id}
    for pattern in _forward(board, segment):
        after = pattern.stations_after(board.station_id)
        if station_id in after:
            passed.update(after[: after.index(station_id)])
    return frozenset(passed)


async def build_facts(
    segment: SubwaySegment, knowledge: StationLookup, alerts: AlertSnapshot
) -> TransferFacts | None:
    at = segment.board_time
    board = await knowledge.station(segment.board_station, at)
    if board is None:
        return None
    candidates = candidate_stations(board, segment)
    limit = asyncio.Semaphore(CONCURRENT_LOOKUPS)

    async def lookup(station_id: str) -> StationService | None:
        async with limit:
            return await knowledge.station(station_id, at)

    found = await asyncio.gather(*(lookup(s) for s in candidates))
    services = {board.station_id: board}
    services.update({s: service for s, service in zip(candidates, found, strict=True) if service})
    names: dict[str, str] = {}
    for service in services.values():
        for station_id, name in service.station_names.items():
            names.setdefault(station_id, name)

    # Lines from each station to every later one, never back over stations
    # the rider has already passed.
    order = [s for s in [board.station_id, *candidates] if s in services]
    targets = [*candidates, segment.alight_station]
    rides: dict[tuple[str, str], tuple[LineOption, ...]] = {}
    for origin in order:
        behind = _behind(board, segment, origin) - {origin}
        for destination in targets:
            if destination == origin:
                continue
            lines = _lines(services[origin], destination, alerts, at, behind)
            if lines:
                rides[(origin, destination)] = lines

    # An interchange is a station more than one line serves on the way: an
    # express stop to change onto the express, or the last stop where it
    # meets the local again to change back.
    def serving(station: str) -> set[str]:
        return {
            line.route_id
            for (origin, destination), lines in rides.items()
            if station in (origin, destination)
            for line in lines
        }

    interchanges = tuple(station for station in order[1:] if len(serving(station)) > 1)
    nodes = {board.station_id, *interchanges, segment.alight_station}
    return TransferFacts(
        segment=segment,
        board_name=board.name,
        alight_name=names.get(segment.alight_station, segment.alight_station),
        direct=rides.get((board.station_id, segment.alight_station), ()),
        interchanges=interchanges,
        names=names,
        rides={k: v for k, v in rides.items() if k[0] in nodes and k[1] in nodes},
    )
