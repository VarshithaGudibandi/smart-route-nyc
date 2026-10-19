"""Turn a transfer proposal into a complete OTP itinerary, or reject it.

OTP's route filter applies to the whole trip, not to one leg, so the check
is one door-to-door query that

* allows only the proposed lines plus the journey's non-subway lines
  (PATH, buses), so the rest of the trip can still be planned, and
* must visit every transfer station of the plan, in order,

under the rider's own settings. A returned itinerary counts only if it
really rides the plan: one of the first ride's lines into the first
transfer station, one of the next ride's lines out of it, and so on to the
end. A "visit" is also satisfied by simply passing through a station, or by
changing between two trains of the same line.

Timing, including the feed's minimum transfer times, is OTP's. Stitching
separately planned pieces together would skip those and overstate savings.
"""

import dataclasses
from datetime import datetime, timedelta
from itertools import pairwise

from app.integrations.otp.client import OtpClient
from app.integrations.otp.mapper import to_route
from app.schemas.routes import Leg, Notice, Route, RouteSearchRequest
from app.schemas.suggestions import (
    ProposalSource,
    RejectedProposal,
    RejectionCode,
    TransferProposal,
    VerifiedSuggestion,
)
from app.services.accessibility.assessor import assess_route
from app.services.accessibility.snapshot import AccessibilitySnapshot
from app.services.route_search import plan_query
from app.services.suggestions.context import SubwaySegment
from app.services.transit.alerts import AlertSnapshot

MIN_SAVING = timedelta(seconds=60)
VERIFY_ITINERARIES = 5


@dataclasses.dataclass(frozen=True)
class VerificationInput:
    request: RouteSearchRequest
    requested: datetime
    segment: SubwaySegment
    baseline_arrival: datetime
    accessibility: AccessibilitySnapshot
    alerts: AlertSnapshot
    now: datetime


def follows(route: Route, proposal: TransferProposal) -> bool:
    """True if consecutive transit legs ride exactly the proposed chain."""
    return _matched(route, proposal.rides, proposal.stations) is not None


def _matched(route: Route, rides: list[list[str]], stations: list[str]) -> list[Leg] | None:
    transit = [leg for leg in route.legs if leg.route_id]
    for start in range(len(transit) - len(rides) + 1):
        legs = transit[start : start + len(rides)]
        if all(leg.route_id in ride for leg, ride in zip(legs, rides, strict=True)) and all(
            into.destination.station_id == station and out_of.origin.station_id == station
            for (into, out_of), station in zip(pairwise(legs), stations, strict=True)
        ):
            return legs
    return None


def boarded_later(route: Route, proposal: TransferProposal) -> TransferProposal | None:
    """The plan from its first transfer station on, if the route rides that.

    Allowed the journey's other lines, OTP often reaches the first transfer
    station without the plan's first ride (PATH to 33rd St, a short walk,
    then the express from 34 St-Penn). That is still the plan's shortcut, so
    it counts, described by what the route actually rides.
    """
    if len(proposal.rides) < 3:
        return None  # what remains would be a single ride, not a transfer
    rides, stations = proposal.rides[1:], proposal.stations[1:]
    legs = _matched(route, rides, stations)
    if legs is None or legs[0].origin.station_id != proposal.stations[0]:
        return None
    reason = "Ride " + ", then ".join(
        f"the {leg.route or leg.route_id} from {leg.origin.name} to {leg.destination.name}"
        if i == 0
        else f"the {leg.route or leg.route_id} to {leg.destination.name}"
        for i, leg in enumerate(legs)
    )
    return TransferProposal(
        first_routes=rides[0],
        transfer_station=stations[0],
        second_routes=rides[1],
        then=proposal.then[1:],
        reason=(reason + ".")[:200],
    )


async def verify(
    otp: OtpClient, proposal: TransferProposal, source: ProposalSource, data: VerificationInput
) -> VerifiedSuggestion | RejectedProposal:
    def reject(code: RejectionCode, detail: str) -> RejectedProposal:
        return RejectedProposal(proposal=proposal, source=source, code=code, detail=detail)

    lines = list(dict.fromkeys(route for ride in proposal.rides for route in ride))
    for route_id in lines:
        if suspended := [
            a for a in data.alerts.for_route(route_id, data.requested) if a.suspends_service
        ]:
            return reject("line_suspended", suspended[0].header[:200])

    query = dataclasses.replace(
        plan_query(data.request, data.requested, VERIFY_ITINERARIES),
        include_routes=tuple(dict.fromkeys([*data.segment.other_route_ids, *lines])),
        via_stations=tuple(proposal.stations),
    )
    plan = await otp.plan(query)
    earliest = data.requested - timedelta(minutes=1)
    max_walk = data.request.accessibility.max_walk_meters
    matching: list[tuple[Route, TransferProposal]] = []
    for route in (to_route(it) for it in plan.itineraries):
        if route.departure < earliest or (max_walk is not None and route.walk_meters > max_walk):
            continue
        if follows(route, proposal):
            matching.append((route, proposal))
        elif (later := boarded_later(route, proposal)) is not None:
            matching.append((route, later))
    if not matching:
        return reject("no_itinerary", "OTP found no itinerary that makes this transfer")

    best, followed = min(matching, key=lambda m: m[0].arrival)
    saving = data.baseline_arrival - best.arrival
    if saving < MIN_SAVING:
        return reject("not_faster", _shortfall(saving))

    assessed = assess_route(
        best, data.accessibility, data.request.accessibility.wheelchair_required, data.now
    )
    if (
        data.request.accessibility.needs_accessible_routing
        and assessed.accessibility_status != "meets"
    ):
        return reject("accessibility", f"accessibility is {assessed.accessibility_status}")

    # Alerts in effect while the rider is on each line.
    alerts = {
        alert.alert_id: alert
        for leg in assessed.legs
        if leg.route_id
        for alert in data.alerts.for_route(leg.route_id, leg.start_time)
    }
    notices = [
        Notice(code="SERVICE_ALERT", message=alert.header[:300]) for alert in alerts.values()
    ]
    return VerifiedSuggestion(
        proposal=followed,
        source=source,
        route=assessed.model_copy(
            update={"origin_type": "model_suggestion" if source == "model" else "rules_suggestion"}
        ),
        saves_seconds=int(saving.total_seconds()),
        notices=notices,
    )


def _shortfall(saving: timedelta) -> str:
    seconds = int(saving.total_seconds())
    if seconds > 0:
        return f"saves only {seconds} s; at least {int(MIN_SAVING.total_seconds())} s is required"
    if seconds == 0:
        return "arrives at the same time as the fastest route"
    return f"arrives {-seconds // 60} min {-seconds % 60} s later than the fastest route"
