"""Convert OTP itineraries into the API's route model."""

import hashlib
from datetime import datetime
from typing import Any

from app.schemas.routes import Leg, Place, Route, StreetFeature, StreetFeatureType

FEATURE_TYPES: dict[str, StreetFeatureType] = {
    "StairsUse": "stairs",
    "EscalatorUse": "escalator",
    "ElevatorUse": "elevator",
    "Entrance": "entrance",
}
# OTP's GTFS-based values; OpenStreetMap "wheelchair=yes/no" maps onto these.
ENTRANCE_ACCESS = {"POSSIBLE": True, "NOT_POSSIBLE": False}


def to_route(itinerary: dict[str, Any]) -> Route:
    legs = [_leg(leg) for leg in itinerary["legs"]]
    return Route(
        route_id=_route_id(legs),
        departure=datetime.fromisoformat(itinerary["start"]),
        arrival=datetime.fromisoformat(itinerary["end"]),
        duration_seconds=int(itinerary["duration"]),
        transfers=int(itinerary["numberOfTransfers"]),
        walk_meters=round(float(itinerary["walkDistance"]), 1),
        walk_seconds=int(itinerary["walkTime"]),
        legs=legs,
    )


def _leg(leg: dict[str, Any]) -> Leg:
    route = leg.get("route") or {}
    start, end = leg["start"], leg["end"]
    return Leg(
        mode=leg["mode"],
        route=route.get("shortName") or route.get("longName"),
        route_id=route.get("gtfsId"),
        agency=(leg.get("agency") or {}).get("name"),
        headsign=leg.get("headsign"),
        trip_id=(leg.get("trip") or {}).get("gtfsId"),
        origin=_place(leg["from"]),
        destination=_place(leg["to"]),
        start_time=_time(start),
        end_time=_time(end),
        realtime=bool(start.get("estimated") or end.get("estimated")),
        distance_meters=round(float(leg["distance"]), 1),
        geometry=(leg.get("legGeometry") or {}).get("points"),
        street_features=_street_features(leg.get("steps") or []),
    )


def _street_features(steps: list[dict[str, Any]]) -> list[StreetFeature]:
    features = []
    for step in steps:
        feature = step.get("feature") or {}
        kind = FEATURE_TYPES.get(feature.get("__typename", ""))
        if kind is None:
            continue
        features.append(
            StreetFeature(
                type=kind,
                lat=step["lat"],
                lon=step["lon"],
                name=feature.get("name"),
                wheelchair_accessible=ENTRANCE_ACCESS.get(feature.get("wheelchairAccessible", "")),
            )
        )
    return features


def _place(place: dict[str, Any]) -> Place:
    stop = place.get("stop") or {}
    parent = stop.get("parentStation") or {}
    return Place(
        name=place.get("name"),
        lat=place["lat"],
        lon=place["lon"],
        stop_id=stop.get("gtfsId"),
        station_id=parent.get("gtfsId") or stop.get("gtfsId"),
    )


def _time(leg_time: dict[str, Any]) -> datetime:
    """Prefer the realtime estimate when OTP has one, else the schedule."""
    estimated = leg_time.get("estimated") or {}
    return datetime.fromisoformat(estimated.get("time") or leg_time["scheduledTime"])


def _route_id(legs: list[Leg]) -> str:
    """Stable ID for the same journey: identical trips at identical times."""
    signature = "|".join(
        f"{leg.mode}:{leg.trip_id or ''}:{leg.start_time.isoformat()}:{leg.end_time.isoformat()}"
        for leg in legs
    )
    return hashlib.sha256(signature.encode()).hexdigest()[:16]
