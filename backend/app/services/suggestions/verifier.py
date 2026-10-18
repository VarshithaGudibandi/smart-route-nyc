"""Turn a transfer proposal into a complete OTP itinerary, or reject it.

OTP's route filter applies to the whole trip, not to one leg, so the check
is one door-to-door query that

* allows only the proposed lines plus the journey's non-subway lines
  (PATH, buses), so the rest of the trip can still be planned, and
* must visit the transfer station,

under the rider's own settings. A returned itinerary counts only if it
really rides one of ``first_routes`` into the transfer station and one of
``second_routes`` out of it: a "visit" is also satisfied by simply ending
a ride there, or by changing between two trains of the same line.

Timing, including the feed's minimum transfer times, is OTP's. Stitching
separately planned pieces together would skip those and overstate savings.
"""

import dataclasses
from datetime import datetime, timedelta
from itertools import pairwise

from app.integrations.otp.client import OtpClient
from app.integrations.otp.mapper import to_route
from app.schemas.routes import Notice, Route, RouteSearchRequest
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
    transit = [leg for leg in route.legs if leg.route_id]
    for into, out_of in pairwise(transit):
        if (
            into.route_id in proposal.first_routes
            and into.destination.station_id == proposal.transfer_station
            and out_of.route_id in proposal.second_routes
            and out_of.origin.station_id == proposal.transfer_station
        ):
            return True
    return False


async def verify(
    otp: OtpClient, proposal: TransferProposal, source: ProposalSource, data: VerificationInput
) -> VerifiedSuggestion | RejectedProposal:
    def reject(code: RejectionCode, detail: str) -> RejectedProposal:
        return RejectedProposal(proposal=proposal, source=source, code=code, detail=detail)

    lines = [*proposal.first_routes, *proposal.second_routes]
    for route_id in lines:
        if suspended := [
            a for a in data.alerts.for_route(route_id, data.requested) if a.suspends_service
        ]:
            return reject("line_suspended", suspended[0].header[:200])

    query = dataclasses.replace(
        plan_query(data.request, data.requested, VERIFY_ITINERARIES),
        include_routes=tuple(dict.fromkeys([*data.segment.other_route_ids, *lines])),
        via_stations=(proposal.transfer_station,),
    )
    plan = await otp.plan(query)
    earliest = data.requested - timedelta(minutes=1)
    matching = [
        route
        for route in (to_route(it) for it in plan.itineraries)
        if route.departure >= earliest and follows(route, proposal)
    ]
    max_walk = data.request.accessibility.max_walk_meters
    if max_walk is not None:
        matching = [r for r in matching if r.walk_meters <= max_walk]
    if not matching:
        return reject("no_itinerary", "OTP found no itinerary that makes this transfer")

    best = min(matching, key=lambda r: r.arrival)
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
        proposal=proposal,
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
