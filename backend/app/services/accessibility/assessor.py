"""Classify each route's step-free accessibility from evidence.

Every station a route boards, alights or transfers at, and every walking leg,
gets a finding: ``meets``, ``violates`` or ``unknown``. The route is
``violates`` if any finding is, ``meets`` only if all are, and ``unknown``
otherwise.

Evidence:
* Subway stations: MTA's elevator inventory and live status. Wheelchair users
  need an ADA-accessible elevator; step-free travel (strollers, luggage) can
  use any working elevator. Escalators are never step-free access.
* Walking legs: stairs and entrance tags in OpenStreetMap, as reported by
  OTP. Absence of mapped stairs is accepted as "no stairs mapped", which is
  how findings word it; OSM can be incomplete.
* Bus stops and PATH stations: no equipment data is published, so they are
  always ``unknown``.
"""

from datetime import datetime

from app.core.freshness import Freshness, SourceKind, classify
from app.schemas.routes import (
    AccessibilityFinding,
    AccessibilityStatus,
    EquipmentNote,
    Leg,
    Place,
    Route,
)
from app.services.accessibility.snapshot import AccessibilitySnapshot, Elevator

SUBWAY_FEED = "MTASBWY"
NO_DATA_DESCRIPTIONS = {
    "PATH": "PATH publishes no elevator data.",
}
BUS_DESCRIPTION = "No accessibility data for bus stops."


def assess_route(
    route: Route, snapshot: AccessibilitySnapshot, wheelchair: bool, now: datetime
) -> Route:
    findings: list[AccessibilityFinding] = []
    seen_stations: set[str] = set()
    for index, leg in enumerate(route.legs):
        if leg.mode == "WALK":
            findings.append(_walk_finding(index, leg))
            continue
        for place in (leg.origin, leg.destination):
            if place.station_id is None or place.station_id in seen_stations:
                continue
            seen_stations.add(place.station_id)
            findings.append(_station_finding(index, place, snapshot, wheelchair, now))

    return route.model_copy(
        update={"accessibility_status": overall(findings), "accessibility_findings": findings}
    )


def overall(findings: list[AccessibilityFinding]) -> AccessibilityStatus:
    statuses = {finding.status for finding in findings}
    if "violates" in statuses:
        return "violates"
    if statuses == {"meets"}:
        return "meets"
    return "unknown"


def _walk_finding(index: int, leg: Leg) -> AccessibilityFinding:
    location = f"Walk from {leg.origin.name or 'start'} to {leg.destination.name or 'end'}"
    stairs = [f for f in leg.street_features if f.type == "stairs"]
    blocked = [
        f for f in leg.street_features if f.type == "entrance" and f.wheelchair_accessible is False
    ]
    if stairs or blocked:
        parts = []
        if stairs:
            parts.append(f"{len(stairs)} flight{'s' if len(stairs) > 1 else ''} of stairs mapped")
        if blocked:
            names = ", ".join(sorted({f.name or "an entrance" for f in blocked}))
            parts.append(f"entrance not wheelchair accessible ({names})")
        status: AccessibilityStatus = "violates"
        text = " and ".join(parts)
        description = text[0].upper() + text[1:] + "."
    else:
        status = "meets"
        description = "No stairs mapped."
    return AccessibilityFinding(
        kind="street",
        location=location,
        leg_index=index,
        status=status,
        description=description,
        source="openstreetmap",
    )


def _station_finding(
    index: int, place: Place, snapshot: AccessibilitySnapshot, wheelchair: bool, now: datetime
) -> AccessibilityFinding:
    assert place.station_id is not None
    feed, _, stop_id = place.station_id.partition(":")
    name = place.name or stop_id

    if feed != SUBWAY_FEED:
        description = NO_DATA_DESCRIPTIONS.get(feed, BUS_DESCRIPTION)
        return AccessibilityFinding(
            kind="station",
            location=name,
            leg_index=index,
            status="unknown",
            description=description,
            source="none",
        )

    def finding(
        status: AccessibilityStatus, description: str, elevators: list[Elevator]
    ) -> AccessibilityFinding:
        return AccessibilityFinding(
            kind="station",
            location=name,
            leg_index=index,
            status=status,
            description=description,
            source="mta_elevators",
            data_as_of=snapshot.observed_at,
            equipment=[_note(e) for e in elevators],
        )

    if not snapshot.loaded:
        return finding("unknown", "Elevator status unavailable.", [])

    kind = "ADA-accessible elevator" if wheelchair else "elevator"
    usable = [
        e for e in snapshot.elevators_by_stop.get(stop_id, []) if e.ada_accessible or not wheelchair
    ]
    if not usable:
        return finding("violates", f"No {kind}.", [])

    freshness = classify(SourceKind.ACCESSIBILITY_STATUS, snapshot.observed_at, None, now)
    if freshness is not Freshness.FRESH:
        return finding(
            "unknown",
            f"Elevator status is {freshness.value}.",
            usable,
        )

    out = [e for e in usable if not e.in_service]
    if len(out) == len(usable):
        every = f"All {len(usable)} {kind}s" if len(usable) > 1 else kind.capitalize()
        return finding("violates", f"{every} out of service.", usable)
    if out:
        return finding(
            "unknown",
            f"{len(out)} of {len(usable)} {kind}s out of service.",
            usable,
        )
    plural = "s" if len(usable) > 1 else ""
    return finding("meets", f"{len(usable)} {kind}{plural} working.", usable)


def _note(elevator: Elevator) -> EquipmentNote:
    return EquipmentNote(
        equipment_id=elevator.equipment_id,
        type="elevator",
        status="in_service" if elevator.in_service else "out_of_service",
        serving=elevator.serving,
        reason=elevator.reason,
        estimated_return_at=elevator.estimated_return_at,
    )
