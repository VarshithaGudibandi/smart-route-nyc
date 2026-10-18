import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.graph_version import GraphVersion
from app.services.transit.knowledge import TransitKnowledge, parse_station, window_start
from tests.fakes import FIXTURES, fake_otp, in_memory_redis
from tests.unit.test_redis_resilience import FailingRedis, make

CHAMBERS = "MTASBWY:137"
TIMES_SQ = "MTASBWY:127"
NINETY_SIXTH = "MTASBWY:120"
SOUTH_FERRY = "MTASBWY:142"
WINDOW_START = datetime.fromisoformat("2026-10-19T08:30:00-04:00")


def fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "otp_station_departures_chambers_st.json").read_text()
    )
    return data


class StationServer:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or fixture()
        self.requests: list[dict[str, Any]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json=self.payload)


def chambers() -> Any:
    return parse_station(fixture()["data"]["station"], WINDOW_START, 3600)


def test_patterns_map_platforms_to_parent_stations() -> None:
    service = chambers()
    assert service.name == "Chambers St"
    assert len(service.patterns) == 13
    assert all(CHAMBERS in p.stations for p in service.patterns)
    assert service.station_names[NINETY_SIXTH] == "96 St"


def test_reaches_respects_direction() -> None:
    service = chambers()
    uptown = {p.route for p in service.patterns_reaching(NINETY_SIXTH)}
    downtown = {p.route for p in service.patterns_reaching(SOUTH_FERRY)}
    assert uptown == {"1", "2", "3"}
    assert downtown == {"1"}
    # A southbound 1 lists 96 St in its stops, but before Chambers St.
    southbound = next(p for p in service.patterns if p.reaches(CHAMBERS, SOUTH_FERRY))
    assert NINETY_SIXTH in southbound.stations
    assert not southbound.reaches(CHAMBERS, NINETY_SIXTH)
    assert NINETY_SIXTH not in southbound.stations_after(CHAMBERS)


def test_headway_is_median_gap_and_needs_two_departures() -> None:
    service = chambers()
    frequent = max(service.patterns, key=lambda p: len(p.departures))
    assert frequent.headway_s is not None and 120 <= frequent.headway_s <= 600
    single = frequent.model_copy(update={"departures": frequent.departures[:1]})
    assert single.headway_s is None


def test_departures_fall_in_the_window() -> None:
    service = chambers()
    start = int(WINDOW_START.timestamp())
    for pattern in service.patterns:
        assert all(start <= t < start + 3600 for t in pattern.departures)


def test_window_start_floors_to_fifteen_minutes_in_new_york() -> None:
    moment = datetime.fromisoformat("2026-10-19T12:44:59+00:00")
    assert window_start(moment).isoformat() == "2026-10-19T08:30:00-04:00"


async def test_sends_window_and_caches_per_graph_version(tmp_path: Path) -> None:
    server = StationServer()
    version_file = tmp_path / "graph-version"
    version_file.write_text("graph-a")
    knowledge = TransitKnowledge(
        fake_otp(server.handler), in_memory_redis(), GraphVersion(version_file)
    )
    around = datetime.fromisoformat("2026-10-19T08:37:00-04:00")

    first = await knowledge.station(CHAMBERS, around)
    second = await knowledge.station(CHAMBERS, around)
    assert first == second
    assert len(server.requests) == 1
    variables = server.requests[0]["variables"]
    assert variables["start"] == int(WINDOW_START.timestamp())
    assert variables["range"] == 3600

    version_file.write_text("graph-b-longer")
    await knowledge.station(CHAMBERS, around)
    assert len(server.requests) == 2


async def test_unknown_station_is_none() -> None:
    server = StationServer({"data": {"station": None, "stop": None}})
    knowledge = TransitKnowledge(fake_otp(server.handler), in_memory_redis(), GraphVersion(None))
    assert await knowledge.station("MTASBWY:XXX", WINDOW_START) is None


async def test_platform_without_parent_station_uses_stop() -> None:
    raw = fixture()["data"]["station"]
    server = StationServer({"data": {"station": None, "stop": raw}})
    knowledge = TransitKnowledge(fake_otp(server.handler), in_memory_redis(), GraphVersion(None))
    service = await knowledge.station(CHAMBERS, WINDOW_START)
    assert service is not None and service.patterns


async def test_works_without_redis() -> None:
    server = StationServer()
    knowledge = TransitKnowledge(fake_otp(server.handler), make(FailingRedis()), GraphVersion(None))
    assert await knowledge.station(TIMES_SQ, WINDOW_START) is not None
    assert await knowledge.station(TIMES_SQ, WINDOW_START) is not None
    assert len(server.requests) == 2
