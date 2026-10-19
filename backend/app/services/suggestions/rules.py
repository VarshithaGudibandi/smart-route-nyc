"""Deterministic transfer plans, and checks every proposal must pass.

The rules generator walks the corridor map in the facts (see
``context.py``): from the boarding station, ride to an interchange, change
to a different line, and so on until the alighting station, with up to
``MAX_RULE_TRANSFERS`` changes. That finds express-then-local, local-then-
express and local-express-local plans alike.

Plans are ranked by an estimate in minutes: stops ridden, plus for every
change a walk between platforms and half the scheduled time between trains
of the line changed to. It is a rough proxy only; OTP's timetable decides
whether a plan is actually faster. ``check`` applies the same ID and
service rules to proposals from any source, including a model.
"""

from dataclasses import dataclass
from itertools import pairwise

from app.schemas.suggestions import (
    MAX_LATER_TRANSFERS,
    MAX_PROPOSALS,
    LaterTransfer,
    RejectionCode,
    TransferProposal,
)
from app.services.suggestions.context import LineOption, TransferFacts

MAX_RULE_TRANSFERS = 2
# Rough averages for ranking only.
MINUTES_PER_STOP = 1.5
TRANSFER_WALK_MINUTES = 2.0
UNKNOWN_HEADWAY_MINUTES = 10.0


_Rides = tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class _Plan:
    stations: tuple[str, ...]
    rides: _Rides
    minutes: float


def propose(
    facts: TransferFacts, limit: int = MAX_PROPOSALS, max_transfers: int = MAX_RULE_TRANSFERS
) -> list[TransferProposal]:
    order = {station: i for i, station in enumerate(facts.stations)}
    alight = facts.segment.alight_station
    plans: list[_Plan] = []

    def walk(at: str, stations: tuple[str, ...], rides: _Rides, minutes: float) -> None:
        for nxt in facts.stations[order[at] + 1 :]:
            for line in facts.lines(at, nxt):
                if rides and line.route_id in rides[-1]:
                    continue
                cost = minutes + line.stops * MINUTES_PER_STOP
                if rides:
                    cost += TRANSFER_WALK_MINUTES + _wait(line)
                ride = (line.route_id,)
                if nxt == alight:
                    if rides:
                        plans.append(_Plan(stations, (*rides, ride), cost))
                elif len(stations) < max_transfers:
                    walk(nxt, (*stations, nxt), (*rides, ride), cost)

    walk(facts.segment.board_station, (), (), 0.0)

    proposals: dict[object, TransferProposal] = {}
    for plan in sorted(plans, key=lambda p: (p.minutes, len(p.stations), p.stations)):
        proposal = _proposal(facts, _widened(facts, plan))
        if check(proposal, facts) is None:
            proposals.setdefault(proposal.signature(), proposal)
        if len(proposals) == limit:
            break
    return list(proposals.values())


def _wait(line: LineOption) -> float:
    """Expected wait for a line: half its scheduled headway."""
    return (line.headway_s / 60 if line.headway_s else UNKNOWN_HEADWAY_MINUTES) / 2


def _widened(facts: TransferFacts, plan: _Plan) -> _Plan:
    """Let each ride use any line making the same number of stops (the 2 or
    the 3), unless that line is the ride before or after it."""
    stops = [facts.segment.board_station, *plan.stations, facts.segment.alight_station]
    rides = []
    for i, (ride, (start, end)) in enumerate(zip(plan.rides, pairwise(stops), strict=True)):
        lines = facts.lines(start, end)
        chosen = next(line for line in lines if line.route_id == ride[0])
        neighbours = {
            *(plan.rides[i - 1] if i else ()),
            *(plan.rides[i + 1] if i + 1 < len(plan.rides) else ()),
        }
        rides.append(
            tuple(
                sorted(
                    line.route_id
                    for line in lines
                    if line.stops == chosen.stops and line.route_id not in neighbours
                )
            )
        )
    return _Plan(plan.stations, tuple(rides), plan.minutes)


def _proposal(facts: TransferFacts, plan: _Plan) -> TransferProposal:
    stations = [*plan.stations, facts.segment.alight_station]
    parts = [
        f"the {_names(facts, ride, start, end)} to {facts.name(end)}"
        for ride, start, end in zip(
            plan.rides, [facts.segment.board_station, *plan.stations], stations, strict=True
        )
    ]
    reason = "Ride " + ", then ".join(parts) + "."
    return TransferProposal(
        first_routes=list(plan.rides[0]),
        transfer_station=plan.stations[0],
        second_routes=list(plan.rides[1]),
        then=[
            LaterTransfer(station=station, routes=list(ride))
            for station, ride in zip(plan.stations[1:], plan.rides[2:], strict=True)
        ],
        reason=reason[:200],
    )


def check(proposal: TransferProposal, facts: TransferFacts) -> tuple[RejectionCode, str] | None:
    """Why a proposal cannot be right for these facts, or None if it may be."""
    order = {station: i for i, station in enumerate(facts.stations)}
    for station in proposal.stations:
        if station not in facts.interchanges:
            return "unknown_id", f"{station} is not a candidate transfer station"
    if len(proposal.then) > MAX_LATER_TRANSFERS:
        return "not_served", "too many transfers"
    positions = [order[s] for s in proposal.stations]
    if positions != sorted(set(positions)):
        return "not_served", "the transfer stations are not in riding order"

    stops = [facts.segment.board_station, *proposal.stations, facts.segment.alight_station]
    rides = proposal.rides
    for routes, (start, end) in zip(rides, pairwise(stops), strict=True):
        serving = {line.route_id for line in facts.lines(start, end)}
        if missing := set(routes) - serving:
            return "not_served", (
                f"{sorted(missing)} do not run from {facts.name(start)} to {facts.name(end)}"
            )
    for before, after in pairwise(rides):
        if set(before) & set(after):
            return "not_served", "the same line on both sides is not a transfer"

    segment = facts.segment
    if (
        tuple(proposal.stations) == segment.transfer_stations
        and len(rides) == len(segment.route_ids)
        and all(route in ride for route, ride in zip(segment.route_ids, rides, strict=True))
    ):
        return "same_as_baseline", "the baseline route already makes this transfer"
    return None


def _names(facts: TransferFacts, routes: tuple[str, ...], start: str, end: str) -> str:
    names = sorted({line.route for line in facts.lines(start, end) if line.route_id in routes})
    return "/".join(names) or "/".join(routes)
