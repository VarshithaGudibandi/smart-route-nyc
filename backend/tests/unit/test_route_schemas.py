from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.routes import RouteSearchRequest


def body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "origin": {"lat": 40.73301, "lon": -74.06289, "label": "Journal Square"},
        "destination": {"lat": 40.80755, "lon": -73.96449},
    }
    return base | overrides


def test_minimal_request_uses_safe_defaults() -> None:
    request = RouteSearchRequest.model_validate(body())
    assert request.depart_at is None
    assert request.accessibility.step_free_required is False
    assert request.optimization.primary == "FASTEST"


@pytest.mark.parametrize(
    "location",
    [{"lat": 42.65, "lon": -73.75}, {"lat": 40.75, "lon": -75.0}, {"lat": 91, "lon": 0}],
)
def test_rejects_locations_outside_service_area(location: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        RouteSearchRequest.model_validate(body(destination=location))


def test_rejects_origin_equal_to_destination() -> None:
    with pytest.raises(ValidationError, match="too close"):
        RouteSearchRequest.model_validate(body(destination={"lat": 40.73302, "lon": -74.06290}))


def test_rejects_departure_without_timezone() -> None:
    with pytest.raises(ValidationError):
        RouteSearchRequest.model_validate(body(depart_at="2026-10-19T08:30:00"))


@pytest.mark.parametrize("offset", [timedelta(hours=-2), timedelta(days=31)])
def test_rejects_departures_outside_window(offset: timedelta) -> None:
    with pytest.raises(ValidationError, match="depart_at"):
        RouteSearchRequest.model_validate(body(depart_at=(datetime.now(UTC) + offset).isoformat()))


def test_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RouteSearchRequest.model_validate(body(user_id="abc"))
