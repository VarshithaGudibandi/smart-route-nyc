from datetime import datetime
from typing import Any

import pytest

from app.schemas.routes import (
    AccessibilityRequirements,
    Location,
    OptimizationPreferences,
    RouteSearchRequest,
)
from app.services.cache_keys import KEY_SCHEMA_VERSION, departure_bucket, route_search_key
from tests.fakes import make_request

SECRET = b"test-secret"
BUCKET = departure_bucket(datetime.fromisoformat("2026-10-19T08:34:00-04:00"))


def key(request: RouteSearchRequest, graph: str = "g1", secret: bytes = SECRET) -> str:
    return route_search_key(request, BUCKET, graph, secret)


def test_departures_bucket_to_five_minutes_in_new_york_time() -> None:
    assert BUCKET.isoformat() == "2026-10-19T08:30:00-04:00"
    utc = datetime.fromisoformat("2026-10-19T12:39:59+00:00")
    assert departure_bucket(utc).isoformat() == "2026-10-19T08:35:00-04:00"


def test_key_is_deterministic_and_opaque() -> None:
    request = make_request()
    assert key(request) == key(make_request())
    assert "40.73" not in key(request)
    assert key(request).startswith(f"srn:v{KEY_SCHEMA_VERSION}:route:")


def test_nearby_coordinates_share_a_key() -> None:
    nudged = make_request().model_copy(update={"origin": Location(lat=40.733012, lon=-74.062893)})
    assert key(nudged) == key(make_request())


def test_label_does_not_affect_key() -> None:
    labeled = make_request().model_copy(
        update={"origin": Location(lat=40.73301, lon=-74.06289, label="Home")}
    )
    assert key(labeled) == key(make_request())


ROUTE_AFFECTING_CHANGES: list[dict[str, Any]] = [
    {"origin": Location(lat=40.7400, lon=-74.06289)},
    {"destination": Location(lat=40.8000, lon=-73.96449)},
    {"accessibility": AccessibilityRequirements(step_free_required=True)},
    {"accessibility": AccessibilityRequirements(wheelchair_required=True)},
    {"accessibility": AccessibilityRequirements(max_walk_meters=500)},
    {"optimization": OptimizationPreferences(primary="FEWEST_TRANSFERS")},
    {"optimization": OptimizationPreferences(primary="LEAST_WALKING")},
    {"optimization": OptimizationPreferences(prefer_fewer_transfers=True)},
    {"optimization": OptimizationPreferences(prefer_less_walking=True)},
]


@pytest.mark.parametrize("change", ROUTE_AFFECTING_CHANGES)
def test_every_route_affecting_field_changes_the_key(change: dict[str, Any]) -> None:
    assert key(make_request().model_copy(update=change)) != key(make_request())


def test_graph_version_and_secret_change_the_key() -> None:
    request = make_request()
    assert key(request, graph="g2") != key(request)
    assert key(request, secret=b"other") != key(request)


def test_departure_bucket_changes_the_key() -> None:
    later = departure_bucket(datetime.fromisoformat("2026-10-19T08:35:00-04:00"))
    assert route_search_key(make_request(), later, "g1", SECRET) != key(make_request())
