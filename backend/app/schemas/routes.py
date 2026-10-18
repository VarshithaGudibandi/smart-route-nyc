"""Request and response models for route search."""

import math
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

# Area covered by the routing graph (NYC plus PATH-served New Jersey).
SERVICE_AREA_SOUTH, SERVICE_AREA_NORTH = 40.48, 40.96
SERVICE_AREA_WEST, SERVICE_AREA_EAST = -74.36, -73.67

MIN_TRIP_DISTANCE_M = 50
MAX_PAST_DEPARTURE = timedelta(hours=1)
MAX_FUTURE_DEPARTURE = timedelta(days=30)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(StrictModel):
    lat: float = Field(ge=SERVICE_AREA_SOUTH, le=SERVICE_AREA_NORTH)
    lon: float = Field(ge=SERVICE_AREA_WEST, le=SERVICE_AREA_EAST)
    # Display name chosen by the user; never sent to the routing engine or logged.
    label: str | None = Field(default=None, max_length=200)


class AccessibilityRequirements(StrictModel):
    step_free_required: bool = False
    wheelchair_required: bool = False
    max_walk_meters: int | None = Field(default=None, ge=100, le=5000)

    @property
    def needs_accessible_routing(self) -> bool:
        return self.step_free_required or self.wheelchair_required


class OptimizationPreferences(StrictModel):
    primary: Literal["FASTEST", "FEWEST_TRANSFERS", "LEAST_WALKING"] = "FASTEST"
    prefer_fewer_transfers: bool = False
    prefer_less_walking: bool = False


class RouteSearchRequest(StrictModel):
    origin: Location
    destination: Location
    depart_at: AwareDatetime | None = None
    accessibility: AccessibilityRequirements = Field(default_factory=AccessibilityRequirements)
    optimization: OptimizationPreferences = Field(default_factory=OptimizationPreferences)

    @model_validator(mode="after")
    def check_trip(self) -> Self:
        if distance_m(self.origin, self.destination) < MIN_TRIP_DISTANCE_M:
            raise ValueError("origin and destination are too close together")
        if self.depart_at is not None:
            now = datetime.now(UTC)
            if not now - MAX_PAST_DEPARTURE <= self.depart_at <= now + MAX_FUTURE_DEPARTURE:
                raise ValueError("depart_at must be between 1 hour ago and 30 days ahead")
        return self


def distance_m(a: Location, b: Location) -> float:
    """Great-circle distance in metres (haversine)."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


class Place(BaseModel):
    name: str | None
    lat: float
    lon: float
    stop_id: str | None = None


class Leg(BaseModel):
    mode: str
    route: str | None = None
    route_id: str | None = None
    agency: str | None = None
    headsign: str | None = None
    trip_id: str | None = None
    origin: Place
    destination: Place
    start_time: datetime
    end_time: datetime
    realtime: bool = False
    distance_meters: float
    # Encoded polyline (Google format, precision 5).
    geometry: str | None = None


class Route(BaseModel):
    route_id: str
    origin_type: Literal["otp_baseline", "ai_proposal_verified"] = "otp_baseline"
    departure: datetime
    arrival: datetime
    duration_seconds: int
    transfers: int
    walk_meters: float
    walk_seconds: int
    # Accessibility is not assessed yet; never report a route as accessible
    # without evidence.
    accessibility_status: Literal["meets", "violates", "unknown"] = "unknown"
    legs: list[Leg]


class Notice(BaseModel):
    code: str
    message: str


class RouteSearchResponse(BaseModel):
    search_id: str
    graph_version: str
    routes: list[Route]
    notices: list[Notice]
