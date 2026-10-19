import itertools
from datetime import UTC, datetime, timedelta

import pytest

from app.schemas.routes import Leg, Place, Route, StreetFeature
from app.services.accessibility.assessor import assess_route
from app.services.accessibility.snapshot import AccessibilitySnapshot, Elevator

NOW = datetime(2026, 10, 19, 12, 30, tzinfo=UTC)
FRESH = NOW - timedelta(minutes=2)


def elevator(eid: str, ada: bool = True, working: bool = True) -> Elevator:
    return Elevator(
        equipment_id=eid,
        ada_accessible=ada,
        in_service=working,
        serving="Street to platform",
        reason=None if working else "Repair",
        estimated_return_at=None,
    )


def snapshot(
    observed_at: datetime | None = FRESH, **stops: list[Elevator]
) -> AccessibilitySnapshot:
    return AccessibilitySnapshot(elevators_by_stop=dict(stops), observed_at=observed_at)


def place(name: str, station: str | None = None) -> Place:
    return Place(name=name, lat=40.7, lon=-74.0, stop_id=station, station_id=station)


def leg(
    mode: str, origin: Place, destination: Place, features: list[StreetFeature] | None = None
) -> Leg:
    return Leg(
        mode=mode,
        origin=origin,
        destination=destination,
        start_time=NOW,
        end_time=NOW,
        distance_meters=100,
        street_features=features or [],
    )


def subway_route(*stations: str, walk_features: list[StreetFeature] | None = None) -> Route:
    places = [place(f"Station {s}", f"MTASBWY:{s}") for s in stations]
    legs = [leg("WALK", place("Home"), places[0], walk_features)]
    legs += [leg("SUBWAY", a, b) for a, b in itertools.pairwise(places)]
    legs.append(leg("WALK", places[-1], place("Work")))
    return Route(
        route_id="r",
        departure=NOW,
        arrival=NOW,
        duration_seconds=0,
        transfers=len(stations) - 2,
        walk_meters=200,
        walk_seconds=120,
        legs=legs,
    )


def statuses(route: Route) -> list[str]:
    return [f.status for f in route.accessibility_findings]


def test_meets_when_every_station_has_a_working_elevator() -> None:
    result = assess_route(
        subway_route("A", "B"), snapshot(A=[elevator("EL1")], B=[elevator("EL2")]), True, NOW
    )
    assert result.accessibility_status == "meets"
    assert statuses(result) == ["meets", "meets", "meets", "meets"]


def test_station_without_elevator_violates() -> None:
    result = assess_route(subway_route("A", "B"), snapshot(A=[elevator("EL1")]), True, NOW)
    assert result.accessibility_status == "violates"
    assert "No ADA-accessible elevator" in result.accessibility_findings[2].description


def test_wheelchair_needs_ada_elevator_but_step_free_does_not() -> None:
    snap = snapshot(A=[elevator("EL1")], B=[elevator("EL2", ada=False)])
    assert assess_route(subway_route("A", "B"), snap, True, NOW).accessibility_status == "violates"
    assert assess_route(subway_route("A", "B"), snap, False, NOW).accessibility_status == "meets"


def test_all_elevators_out_violates() -> None:
    snap = snapshot(A=[elevator("EL1")], B=[elevator("EL2", working=False)])
    result = assess_route(subway_route("A", "B"), snap, True, NOW)
    assert result.accessibility_status == "violates"
    out = result.accessibility_findings[2]
    assert out.equipment[0].status == "out_of_service"
    assert out.equipment[0].reason == "Repair"


def test_some_elevators_out_is_unknown() -> None:
    snap = snapshot(A=[elevator("EL1")], B=[elevator("EL2"), elevator("EL3", working=False)])
    result = assess_route(subway_route("A", "B"), snap, True, NOW)
    assert result.accessibility_status == "unknown"
    assert "1 of 2" in result.accessibility_findings[2].description


@pytest.mark.parametrize("age", [timedelta(minutes=11), timedelta(hours=2)])
def test_stale_elevator_data_cannot_confirm_access(age: timedelta) -> None:
    snap = snapshot(observed_at=NOW - age, A=[elevator("EL1")], B=[elevator("EL2")])
    result = assess_route(subway_route("A", "B"), snap, True, NOW)
    assert result.accessibility_status == "unknown"


def test_missing_elevator_data_is_unknown() -> None:
    result = assess_route(subway_route("A", "B"), snapshot(observed_at=None), True, NOW)
    assert result.accessibility_status == "unknown"


def test_stairs_on_walk_violate() -> None:
    stairs = [StreetFeature(type="stairs", lat=40.7, lon=-74.0)]
    snap = snapshot(A=[elevator("EL1")], B=[elevator("EL2")])
    result = assess_route(subway_route("A", "B", walk_features=stairs), snap, True, NOW)
    assert result.accessibility_status == "violates"
    assert result.accessibility_findings[0].source == "openstreetmap"


def test_inaccessible_entrance_violates_but_unknown_entrance_does_not() -> None:
    snap = snapshot(A=[elevator("EL1")], B=[elevator("EL2")])
    blocked = [StreetFeature(type="entrance", lat=1, lon=1, name="NE", wheelchair_accessible=False)]
    untagged = [StreetFeature(type="entrance", lat=1, lon=1, name="NE")]
    assert (
        assess_route(
            subway_route("A", "B", walk_features=blocked), snap, True, NOW
        ).accessibility_status
        == "violates"
    )
    assert (
        assess_route(
            subway_route("A", "B", walk_features=untagged), snap, True, NOW
        ).accessibility_status
        == "meets"
    )


def test_transfer_station_is_assessed_once() -> None:
    snap = snapshot(A=[elevator("1")], B=[elevator("2")], C=[elevator("3")])
    result = assess_route(subway_route("A", "B", "C"), snap, True, NOW)
    stations = [f.location for f in result.accessibility_findings if f.kind == "station"]
    assert stations == ["Station A", "Station B", "Station C"]


def test_operators_without_data_are_unknown() -> None:
    path, bus = place("Journal Square", "PATH:781722"), place("Broadway", "MTAMN:401")
    route = subway_route("A", "B").model_copy(
        update={"legs": [leg("SUBWAY", path, place("WTC", "PATH:781747")), leg("BUS", bus, bus)]}
    )
    result = assess_route(route, snapshot(), True, NOW)
    assert result.accessibility_status == "unknown"
    assert all(f.source == "none" for f in result.accessibility_findings)
    assert "PATH" in result.accessibility_findings[0].description
