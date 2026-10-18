"""Facts about the transfers a journey's subway segment could make.

The investigated segment is the run of consecutive subway legs in the
fastest baseline route, from where the rider boards the subway to where
they leave it. Everything else in the journey (PATH, buses, walking) is
left to OTP when a proposal is verified.

Candidate transfer stations are the stations the rider passes heading
toward the destination. A candidate is kept only if changing there gives
access to a line the rider could not simply stay on: an express that skips
stops, or a line that does not serve the boarding station.

For each candidate, the facts list which lines run from the boarding
station to it and which lines run from it to the alighting station, with
stop counts and scheduled headways. Every fact comes from the routing
graph's timetable; IDs in the facts are the only IDs a proposal may use.

Only single-transfer changes are considered: a trip that would need two
extra transfers (local, express, local) is out of scope.
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
class TransferOption:
    station_id: str
    name: str
    # Lines from the boarding station to this station, and from it onward.
    to_transfer: tuple[LineOption, ...]
    to_destination: tuple[LineOption, ...]


@dataclass(frozen=True)
class TransferFacts:
    segment: SubwaySegment
    board_name: str
    alight_name: str
    direct: tuple[LineOption, ...]
    options: tuple[TransferOption, ...]

    def option(self, station_id: str) -> TransferOption | None:
        return next((o for o in self.options if o.station_id == station_id), None)


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
    there, or if after ``origin`` it runs back over ``behind`` (stations the
    rider has already passed), which means it is heading the wrong way."""
    try:
        start = pattern.stations.index(origin)
        end = pattern.stations.index(destination, start + 1)
    except ValueError:
        return None
    if behind & set(pattern.stations[start + 1 :]):
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
    """Stations the rider passes in the right direction, nearest first.

    "Forward" patterns are those from the boarding station that later reach
    the alighting station or a station where the baseline route transfers;
    a pattern heading the other way reaches neither. Every station after the
    boarding station on a forward pattern is a candidate, up to the cap.
    """
    distance: dict[str, int] = {}
    for pattern in _forward(board, segment):
        after = pattern.stations_after(board.station_id)
        for offset, station in enumerate(after, start=1):
            distance[station] = min(distance.get(station, offset), offset)
    distance.pop(segment.alight_station, None)
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

    services = await asyncio.gather(*(lookup(s) for s in candidates))

    options = []
    for station_id, service in zip(candidates, services, strict=True):
        if service is None:
            continue
        to_transfer = _lines(board, station_id, alerts, at)
        # An onward line that runs back over stations already passed means the
        # rider overshot or rode away from the destination first.
        to_destination = _lines(
            service, segment.alight_station, alerts, at, _behind(board, segment, station_id)
        )
        reach = {line.route_id for line in to_transfer}
        onward = {line.route_id for line in to_destination}
        # Kept only if changing here gives access to a line the rider would
        # not otherwise have: an express that skips stops, or a new line.
        if not to_transfer or not onward or reach == onward:
            continue
        options.append(
            TransferOption(
                station_id=station_id,
                name=board.station_names.get(station_id, station_id),
                to_transfer=to_transfer,
                to_destination=to_destination,
            )
        )
    alight_name = next(
        (
            s.station_names[segment.alight_station]
            for s in [board, *filter(None, services)]
            if segment.alight_station in s.station_names
        ),
        segment.alight_station,
    )
    return TransferFacts(
        segment=segment,
        board_name=board.name,
        alight_name=alight_name,
        direct=_lines(board, segment.alight_station, alerts, at),
        options=tuple(options),
    )
