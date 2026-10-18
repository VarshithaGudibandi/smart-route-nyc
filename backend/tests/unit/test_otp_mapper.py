from typing import Any

from app.integrations.otp.mapper import to_route
from tests.fakes import plan_fixture


def itineraries() -> list[dict[str, Any]]:
    return [edge["node"] for edge in plan_fixture()["data"]["planConnection"]["edges"]]


def test_maps_recorded_journal_square_itinerary() -> None:
    route = to_route(itineraries()[0])

    assert route.origin_type == "otp_baseline"
    assert route.accessibility_status == "unknown"
    assert route.departure.tzinfo is not None
    assert route.duration_seconds == (route.arrival - route.departure).total_seconds()
    modes = [leg.mode for leg in route.legs]
    assert modes[0] == "WALK" and modes[-1] == "WALK"
    transit = [leg for leg in route.legs if leg.route_id]
    assert {leg.route_id.split(":")[0] for leg in transit if leg.route_id} == {"PATH", "MTASBWY"}
    assert all(leg.geometry for leg in route.legs)
    assert route.transfers == len(transit) - 1


def test_route_ids_are_stable_and_distinct() -> None:
    first, second = itineraries()
    assert to_route(first).route_id == to_route(first).route_id
    assert to_route(first).route_id != to_route(second).route_id


def test_maps_stairs_and_parent_stations() -> None:
    route = to_route(itineraries()[0])
    features = [f.type for leg in route.legs for f in leg.street_features]
    assert "stairs" in features
    subway = [leg for leg in route.legs if leg.route_id and leg.route_id.startswith("MTASBWY")]
    assert subway[0].destination.station_id == "MTASBWY:117"
