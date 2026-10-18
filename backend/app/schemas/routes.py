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
    # Parent station when the stop is a platform (MTA "120S" -> station "120").
    station_id: str | None = None


StreetFeatureType = Literal["stairs", "escalator", "elevator", "entrance"]


class StreetFeature(BaseModel):
    """Notable point on a walking leg, as mapped in OpenStreetMap."""

    type: StreetFeatureType
    lat: float
    lon: float
    name: str | None = None
    # For entrances: OpenStreetMap's wheelchair tag, when present.
    wheelchair_accessible: bool | None = None


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
    street_features: list[StreetFeature] = Field(default_factory=list)


AccessibilityStatus = Literal["meets", "violates", "unknown"]


class EquipmentNote(BaseModel):
    equipment_id: str
    type: Literal["elevator", "escalator"]
    status: Literal["in_service", "out_of_service"]
    serving: str | None = None
    reason: str | None = None
    estimated_return_at: datetime | None = None


class AccessibilityFinding(BaseModel):
    """Evidence for one place on a route: a station or a walking leg."""

    kind: Literal["station", "street"]
    location: str
    leg_index: int
    status: AccessibilityStatus
    description: str
    source: Literal["mta_elevators", "openstreetmap", "none"]
    data_as_of: datetime | None = None
    equipment: list[EquipmentNote] = Field(default_factory=list)


class Route(BaseModel):
    route_id: str
    # How the journey was found. Suggestions are always full OTP itineraries;
    # the label says whether the transfer idea came from rules or a model.
    origin_type: Literal["otp_baseline", "rules_suggestion", "model_suggestion"] = "otp_baseline"
    departure: datetime
    arrival: datetime
    duration_seconds: int
    transfers: int
    walk_meters: float
    walk_seconds: int
    # Assessed on every response from current evidence; "unknown" until then.
    accessibility_status: AccessibilityStatus = "unknown"
    accessibility_findings: list[AccessibilityFinding] = Field(default_factory=list)
    legs: list[Leg]


class Notice(BaseModel):
    code: str
    message: str


class DataFreshness(BaseModel):
    source: str
    freshness: Literal["fresh", "stale", "expired", "unknown"]
    as_of: datetime | None


class RouteSearchResponse(BaseModel):
    search_id: str
    graph_version: str
    routes: list[Route]
    # When step-free or wheelchair access is required: routes that do not meet
    # it, or cannot be confirmed to, kept separate and never presented as compliant.
    review_only_routes: list[Route] = Field(default_factory=list)
    notices: list[Notice]
    data_freshness: list[DataFreshness] = Field(default_factory=list)
