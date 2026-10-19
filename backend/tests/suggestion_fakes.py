"""A small synthetic subway, and OTP-shaped itineraries on it. DEMO DATA.

Local line L stops at A, B, C, D, E; express X stops at A, C, E (both
northbound, plus a southbound L). A rider from A to D can ride L all the
way, or take X to C and change to L.

A second corridor tests a change there and back: local 1 stops at P1 to P7,
express 2 stops at P2, P4 and P6, then leaves the corridor for Z. A rider
from P1 to P7 can ride the 1, or change to the 2 at P2 and back to the 1 at
P6.
"""

from datetime import datetime, timedelta
from typing import Any

from app.services.transit.knowledge import PatternService, StationService

T0 = datetime.fromisoformat("2026-10-19T08:30:00-04:00")
LOCAL, EXPRESS = "MTASBWY:L", "MTASBWY:X"
A, B, C, D, E = (f"MTASBWY:{s}" for s in "ABCDE")
NORTH_L = (A, B, C, D, E)
NORTH_X = (A, C, E)
SOUTH_L = (E, D, C, B, A)
ONE, TWO = "MTASBWY:1", "MTASBWY:2"
P1, P2, P3, P4, P5, P6, P7 = (f"MTASBWY:P{i}" for i in range(1, 8))
Z = "MTASBWY:Z"
LOCAL_1 = (P1, P2, P3, P4, P5, P6, P7)
EXPRESS_2 = (P2, P4, P6, Z)


def pattern(route_id: str, code: str, stations: tuple[str, ...], every_s: int) -> PatternService:
    start = int(T0.timestamp())
    return PatternService(
        pattern=code,
        route_id=route_id,
        route=route_id.split(":")[1],
        mode="SUBWAY",
        direction_id=1 if stations[0] == E else 0,
        headsign=None,
        stations=stations,
        departures=tuple(range(start, start + 3600, every_s)),
    )


PATTERNS = {
    "north_l": pattern(LOCAL, "L:0", NORTH_L, 360),
    "north_x": pattern(EXPRESS, "X:0", NORTH_X, 300),
    "south_l": pattern(LOCAL, "L:1", SOUTH_L, 360),
    "local_1": pattern(ONE, "1:0", LOCAL_1, 300),
    "express_2": pattern(TWO, "2:0", EXPRESS_2, 240),
}
KNOWN = {*NORTH_L, *LOCAL_1, Z}


def station(station_id: str) -> StationService:
    serving = [p for p in PATTERNS.values() if station_id in p.stations[:-1]]
    return StationService(
        station_id=station_id,
        name=f"{station_id.split(':')[1]} St",
        window_start=T0,
        window_s=3600,
        patterns=tuple(serving),
        station_names={s: f"{s.split(':')[1]} St" for p in serving for s in p.stations},
    )


class FakeKnowledge:
    def __init__(self) -> None:
        self.lookups: list[str] = []

    async def station(self, station_id: str, around: datetime) -> StationService | None:
        self.lookups.append(station_id)
        return station(station_id) if station_id in KNOWN else None


def _place(station_id: str | None, lat: float = 40.75, lon: float = -73.99) -> dict[str, Any]:
    stop = {"gtfsId": f"{station_id}1", "parentStation": {"gtfsId": station_id}}
    return {"name": station_id, "lat": lat, "lon": lon, "stop": stop if station_id else None}


def _time(moment: datetime) -> dict[str, Any]:
    return {"scheduledTime": moment.isoformat(), "estimated": None}


def itinerary(rides: list[tuple[str, str, str, int, int]], walk_end_min: int = 3) -> dict[str, Any]:
    """OTP itinerary JSON: (route, from, to, start_min, end_min) rides after T0,
    followed by a short walk to the destination."""
    legs: list[dict[str, Any]] = []
    for route_id, origin, destination, start, end in rides:
        legs.append(
            {
                "mode": "SUBWAY",
                "distance": 1000.0,
                "headsign": None,
                "route": {"gtfsId": route_id, "shortName": route_id.split(":")[1]},
                "agency": {"name": "MTA New York City Transit"},
                "trip": {"gtfsId": f"{route_id}-{start}"},
                "from": _place(origin),
                "to": _place(destination),
                "start": _time(T0 + timedelta(minutes=start)),
                "end": _time(T0 + timedelta(minutes=end)),
                "legGeometry": None,
                "steps": [],
            }
        )
    last = T0 + timedelta(minutes=rides[-1][4])
    legs.append(
        {
            "mode": "WALK",
            "distance": 200.0,
            "route": None,
            "from": _place(rides[-1][2]),
            "to": {"name": "Destination", "lat": 40.76, "lon": -73.98, "stop": None},
            "start": _time(last),
            "end": _time(last + timedelta(minutes=walk_end_min)),
            "steps": [],
        }
    )
    arrival = last + timedelta(minutes=walk_end_min)
    begin = T0 + timedelta(minutes=rides[0][3])
    return {
        "start": begin.isoformat(),
        "end": arrival.isoformat(),
        "duration": int((arrival - begin).total_seconds()),
        "numberOfTransfers": len(rides) - 1,
        "walkDistance": 200.0,
        "walkTime": walk_end_min * 60,
        "legs": legs,
    }


def plan_response(*itineraries: dict[str, Any]) -> dict[str, Any]:
    return {
        "data": {
            "planConnection": {
                "edges": [{"node": it} for it in itineraries],
                "routingErrors": [],
            }
        }
    }
