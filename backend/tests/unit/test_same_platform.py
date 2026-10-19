import json
from pathlib import Path

from app.integrations.otp.mapper import to_route
from app.services.transit.same_platform import SamePlatformChanges
from tests.suggestion_fakes import EXPRESS, LOCAL, A, C, D, itinerary


def changes(tmp_path: Path, minimum: int = 180) -> SamePlatformChanges:
    path = tmp_path / "same-platform-transfers.json"
    stations = {"C": {"name": "C St", "routes": ["L", "X"], "mta_min_transfer_s": minimum}}
    path.write_text(json.dumps({"change_seconds": 60, "stations": stations}))
    return SamePlatformChanges(path)


def test_a_change_faster_than_mta_allows_is_labelled(tmp_path: Path) -> None:
    # Off the X at C at minute 5, onto the L at minute 7: 2 min, MTA allows 3.
    route = to_route(itinerary([(EXPRESS, A, C, 1, 5), (LOCAL, C, D, 7, 10)]))
    notes = changes(tmp_path).label(route).notes
    assert [n.code for n in notes] == ["SAME_PLATFORM_CHANGE"]
    assert notes[0].message == (
        "Assumes a 1-minute change across the platform at C St; MTA's timetable allows 3 min."
    )


def test_a_change_mta_already_allows_is_not_labelled(tmp_path: Path) -> None:
    route = to_route(itinerary([(EXPRESS, A, C, 1, 5), (LOCAL, C, D, 9, 12)]))
    assert changes(tmp_path).label(route).notes == []


def test_without_the_station_list_nothing_is_labelled(tmp_path: Path) -> None:
    route = to_route(itinerary([(EXPRESS, A, C, 1, 5), (LOCAL, C, D, 6, 9)]))
    assert SamePlatformChanges(tmp_path / "missing.json").label(route).notes == []
    assert SamePlatformChanges(None).label(route).notes == []
