"""The facts an explanation may state about one route.

Every fact is built by code from the route itself, its accessibility
findings, current alerts on its lines and the age of the data. Each has a
stable ID so a model's sentences can cite it, and ``required`` marks facts
an explanation must not leave out: barriers, unconfirmed steps and
alerts. Omitting a barrier misleads as much as inventing one.

The texts are complete sentences, so the facts alone are a usable
explanation when no model is configured or a model's wording is rejected.
"""

from datetime import datetime, timedelta
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel

from app.core.freshness import Freshness, SourceKind, classify
from app.schemas.routes import AccessibilityFinding, Leg, Route
from app.services.accessibility.snapshot import AccessibilitySnapshot
from app.services.cache_keys import NEW_YORK
from app.services.transit.alerts import AlertSnapshot

FactKind = Literal[
    "summary", "origin", "leg", "transfer", "accessibility", "finding", "alert", "data"
]

ACCESSIBILITY_SUMMARY = {
    "meets": "Every station and walk on this route was checked and meets the requirement.",
    "violates": "This route has at least one known barrier, listed below.",
    "unknown": "Some stations or walks on this route could not be confirmed, listed below.",
}
# Beyond this, current elevator status may not hold when the rider travels.
FUTURE_TRIP = timedelta(hours=1)
SOURCE_NAMES = {"mta_elevators": "MTA elevator status", "openstreetmap": "OpenStreetMap"}


class Fact(BaseModel):
    id: str
    kind: FactKind
    text: str
    required: bool = False


# OTP's names for coordinate endpoints, which are not places a rider knows.
ENDPOINT_NAMES = {"Origin": "your starting point", "Destination": "your destination"}


def _place(name: str | None, fallback: str) -> str:
    return ENDPOINT_NAMES.get(name or "", name or fallback)


def _clock(moment: datetime) -> str:
    return moment.astimezone(NEW_YORK).strftime("%H:%M")


def _minutes(delta: timedelta) -> int:
    return max(0, round(delta.total_seconds() / 60))


def _line(leg: Leg) -> str:
    name = f"the {leg.route}" if leg.route else f"the {leg.mode.lower()}"
    return f"{name} ({leg.agency})" if leg.agency else name


def route_facts(
    route: Route,
    accessibility_required: bool,
    accessibility: AccessibilitySnapshot,
    alerts: AlertSnapshot,
    now: datetime,
) -> list[Fact]:
    facts = [
        Fact(
            id="summary",
            kind="summary",
            text=(
                f"Leaves at {_clock(route.departure)} and arrives at {_clock(route.arrival)}: "
                f"{_minutes(route.arrival - route.departure)} min in total, with "
                f"{route.transfers} transfer{'' if route.transfers == 1 else 's'} and "
                f"{round(route.walk_meters)} m of walking."
            ),
        )
    ]
    if route.origin_type != "otp_baseline":
        facts.append(
            Fact(
                id="origin",
                kind="origin",
                text=(
                    "This route makes a transfer the planner did not pick by default; "
                    "the routing engine checked it against the timetable and it arrives earlier."
                ),
            )
        )

    transit = [(i, leg) for i, leg in enumerate(route.legs) if leg.route_id]
    for i, leg in enumerate(route.legs):
        origin = _place(leg.origin.name, "your starting point")
        destination = _place(leg.destination.name, "your destination")
        if leg.mode == "WALK":
            text = (
                f"Walk {round(leg.distance_meters)} m (about "
                f"{max(1, _minutes(leg.end_time - leg.start_time))} min) from {origin} "
                f"to {destination}."
            )
        else:
            toward = f" toward {leg.headsign}" if leg.headsign else ""
            text = (
                f"Take {_line(leg)}{toward} from {origin} at {_clock(leg.start_time)} "
                f"to {destination}, arriving at {_clock(leg.end_time)}."
            )
        facts.append(Fact(id=f"leg{i}", kind="leg", text=text))

    for (_, into), (j, out_of) in pairwise(transit):
        gap = _minutes(out_of.start_time - into.end_time)
        facts.append(
            Fact(
                id=f"transfer{j}",
                kind="transfer",
                text=(
                    f"Change from the {into.route} at {into.destination.name} to the "
                    f"{out_of.route} at {out_of.origin.name}, with {gap} min between trains."
                ),
            )
        )

    if route.accessibility_findings:
        status = route.accessibility_status
        facts.append(
            Fact(
                id="accessibility",
                kind="accessibility",
                text=ACCESSIBILITY_SUMMARY[status],
                required=accessibility_required or status != "meets",
            )
        )
    for k, finding in enumerate(route.accessibility_findings):
        facts.append(
            Fact(
                id=f"finding{k}",
                kind="finding",
                text=_finding_text(finding),
                required=finding.status != "meets",
            )
        )

    # Alerts in effect when the rider is on each line, not when they ask:
    # planned work tonight says nothing about a trip tomorrow morning.
    seen: set[str] = set()
    for _, leg in transit:
        for alert in alerts.for_route(leg.route_id or "", leg.start_time):
            if alert.alert_id in seen:
                continue
            seen.add(alert.alert_id)
            facts.append(
                Fact(
                    id=f"alert{len(seen) - 1}",
                    kind="alert",
                    text=f"MTA service alert: {alert.header}",
                    required=True,
                )
            )

    facts.extend(_data_facts(route, accessibility, now))
    return facts


def _finding_text(finding: AccessibilityFinding) -> str:
    label = {"meets": "meets the requirement", "violates": "barrier", "unknown": "not confirmed"}
    location = finding.location
    for raw, friendly in ENDPOINT_NAMES.items():
        location = location.replace(f" {raw}", f" {friendly}")
    text = f"{location}: {label[finding.status]}. {finding.description}"
    source = SOURCE_NAMES.get(finding.source)
    if source and finding.data_as_of:
        text += f" ({source} as of {_clock(finding.data_as_of)})"
    elif source:
        text += f" (source: {source})"
    return text


def _data_facts(route: Route, accessibility: AccessibilitySnapshot, now: datetime) -> list[Fact]:
    facts = []
    uses_elevator_data = any(f.source == "mta_elevators" for f in route.accessibility_findings)
    if uses_elevator_data and route.departure - now > FUTURE_TRIP:
        facts.append(
            Fact(
                id="status_now",
                kind="data",
                text=(
                    "Elevator status is as it is now; it can change before you travel at "
                    f"{_clock(route.departure)}."
                ),
            )
        )
    if uses_elevator_data:
        freshness = classify(SourceKind.ACCESSIBILITY_STATUS, accessibility.observed_at, None, now)
        if freshness is not Freshness.FRESH:
            facts.append(
                Fact(
                    id="elevator_data",
                    kind="data",
                    text=(
                        f"Elevator status data is {freshness.value}, so station findings "
                        "may not reflect current conditions."
                    ),
                    required=True,
                )
            )
    if not any(leg.realtime for leg in route.legs):
        facts.append(
            Fact(
                id="schedule",
                kind="data",
                text="Times come from the published timetable, not live train positions.",
            )
        )
    return facts
