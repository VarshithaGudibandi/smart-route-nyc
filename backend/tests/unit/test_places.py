import json
from typing import Any

import httpx
import pytest

from app.core.graph_version import GraphVersion
from app.core.resilience import CircuitBreaker
from app.services.places.photon import (
    BBOX,
    PhotonGeocoder,
    PlaceSearchUnavailableError,
    parse_features,
)
from app.services.places.service import PlaceSearch
from app.services.places.stations import StationIndex, build_index, search, tokens
from tests.fakes import FIXTURES, fake_otp, in_memory_redis


def transit_places() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "otp_transit_places.json").read_text())
    return data


def photon_payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "photon_search.json").read_text())
    return data


def names(query: str) -> list[str]:
    return [s.name for s in search(build_index(transit_places()["data"]), query, 5)]


# --- stations -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("34th Street", ["34", "st"]),
        ("Times Square", ["times", "sq"]),
        ("116 St-Columbia University", ["116", "st", "columbia", "university"]),
        ("Jackson Hts\u2013Roosevelt Av", ["jackson", "hts", "roosevelt", "av"]),
        ("Avenue", ["av"]),
    ],
)
def test_tokens_normalize_abbreviations(text: str, expected: list[str]) -> None:
    assert tokens(text) == expected


@pytest.mark.parametrize(
    ("query", "first"),
    [
        ("herald sq", "34 St-Herald Sq"),
        ("Herald Square", "34 St-Herald Sq"),
        ("times sq", "Times Sq-42 St"),
        ("columbia", "116 St-Columbia University"),
        ("journal", "Journal Square"),
        ("bay ridge 95", "Bay Ridge-95 St"),
    ],
)
def test_station_search_matches_word_prefixes(query: str, first: str) -> None:
    assert names(query)[0] == first


def test_station_complexes_merge_but_distant_namesakes_do_not() -> None:
    index = {(s.name, s.detail) for s in build_index(transit_places()["data"])}
    # Herald Sq is two GTFS stations (6 Av and Broadway lines): one result.
    # C and E appear because planned-work reroutes are in the timetable.
    assert [d for n, d in index if n == "34 St-Herald Sq"] == ["Subway · B C D E F M N Q R W"]
    # The 96 Sts are on different avenues, far apart.
    assert len([n for n, _ in index if n == "96 St"]) >= 3


def test_path_platforms_become_one_station_with_their_lines() -> None:
    path = {s.name: s for s in build_index(transit_places()["data"]) if s.detail.startswith("PATH")}
    assert len(path) == 13
    assert "Newark\u2013WTC" in path["Journal Square"].detail
    assert names("wtc")[:2] == ["WTC Cortlandt", "World Trade Center"]


def test_no_match_and_empty_query() -> None:
    assert names("zzzz") == []
    assert names("  ") == []


async def test_index_reloads_when_the_graph_changes(tmp_path: Any) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=transit_places())

    version = tmp_path / "graph-version"
    version.write_text("a")
    index = StationIndex(fake_otp(handler), GraphVersion(version))
    await index.search("herald", 3)
    await index.search("times", 3)
    assert calls == 1
    version.write_text("bb")
    await index.search("herald", 3)
    assert calls == 2


# --- geocoder -----------------------------------------------------------------


def test_parse_keeps_results_in_the_service_area_with_a_readable_detail() -> None:
    results = parse_features(photon_payload())
    assert [r.name for r in results] == ["Columbia University", "151 West 34th Street"]
    assert results[0].detail == "Broadway, Manhattan, New York"
    assert results[1].detail == "Manhattan, New York"
    assert all(r.source == "openstreetmap" and r.kind == "place" for r in results)


class PhotonServer:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, json=photon_payload())


def geocoder(server: PhotonServer, threshold: int = 3) -> PhotonGeocoder:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(server.handler), base_url="http://photon"
    )
    return PhotonGeocoder(
        http, CircuitBreaker("geocoder", threshold, 60), in_memory_redis(), b"s", 60
    )


async def test_geocoder_limits_to_the_area_and_caches() -> None:
    server = PhotonServer()
    coder = geocoder(server)
    first = await coder.search("columbia", 4)
    second = await coder.search("  Columbia ", 4)
    assert first == second and len(server.requests) == 1
    params = server.requests[0].url.params
    assert params["bbox"] == BBOX and params["q"] == "columbia" and params["limit"] == "4"


async def test_geocoder_failures_open_the_breaker() -> None:
    server = PhotonServer(status=503)
    coder = geocoder(server, threshold=2)
    for _ in range(3):
        with pytest.raises(PlaceSearchUnavailableError):
            await coder.search("columbia", 4)
    assert len(server.requests) == 2  # the third call never left


# --- combined -----------------------------------------------------------------


async def stations_index() -> StationIndex:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=transit_places())

    return StationIndex(fake_otp(handler), GraphVersion(None))


async def test_stations_come_first_then_places_with_attribution() -> None:
    response = await PlaceSearch(await stations_index(), geocoder(PhotonServer())).search(
        "columbia", 6
    )
    assert [r.source for r in response.results] == ["transit", "openstreetmap", "openstreetmap"]
    assert response.attribution == "© OpenStreetMap contributors"
    assert response.notices == []


async def test_geocoder_outage_still_returns_stations_with_a_notice() -> None:
    response = await PlaceSearch(await stations_index(), geocoder(PhotonServer(503))).search(
        "columbia", 6
    )
    assert [r.name for r in response.results] == ["116 St-Columbia University"]
    assert [n.code for n in response.notices] == ["ADDRESS_SEARCH_UNAVAILABLE"]
    assert response.attribution is None


async def test_routing_outage_still_returns_places() -> None:
    async def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    index = StationIndex(fake_otp(down), GraphVersion(None))
    response = await PlaceSearch(index, geocoder(PhotonServer())).search("columbia", 6)
    assert [r.source for r in response.results] == ["openstreetmap", "openstreetmap"]
    assert [n.code for n in response.notices] == ["STATIONS_UNAVAILABLE"]
