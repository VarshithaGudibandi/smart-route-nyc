"""Deterministic transfer proposals, and checks every proposal must pass.

The rules generator proposes, for each candidate station, the transfer
between the lines that meet there, in two shapes:

* express then local: lines that reach the transfer station but not the
  destination, then the lines that continue to it;
* local then express: the lines that reach the transfer station, then lines
  from there that the rider could not have boarded at the start.

Proposals are ranked by estimated stops ridden plus a transfer penalty, a
rough proxy only: OTP's timetable decides whether one is actually faster.
``check`` applies the same ID and service rules to proposals from any
source, including a model.
"""

from app.schemas.suggestions import MAX_PROPOSALS, RejectionCode, TransferProposal
from app.services.suggestions.context import TransferFacts

# A transfer costs roughly this many stops of riding (walk plus wait).
TRANSFER_PENALTY_STOPS = 3


def propose(facts: TransferFacts, limit: int = MAX_PROPOSALS) -> list[TransferProposal]:
    scored: dict[tuple[frozenset[str], str, frozenset[str]], tuple[int, TransferProposal]] = {}
    for option in facts.options:
        reach = {line.route_id: line.stops for line in option.to_transfer}
        onward = {line.route_id: line.stops for line in option.to_destination}
        shapes = (
            ({r for r in reach if r not in onward}, set(onward)),
            (set(reach), {r for r in onward if r not in reach}),
        )
        for first, second in shapes:
            if not first or not second:
                continue
            proposal = TransferProposal(
                first_routes=sorted(first),
                transfer_station=option.station_id,
                second_routes=sorted(second),
                reason=(
                    f"Ride the {_names(facts, first, option.station_id, True)} to "
                    f"{option.name}, then change to the "
                    f"{_names(facts, second, option.station_id, False)} for {facts.alight_name}."
                )[:200],
            )
            if check(proposal, facts) is not None:
                continue
            score = min(reach[r] for r in first) + min(onward[r] for r in second)
            scored.setdefault(proposal.signature(), (score + TRANSFER_PENALTY_STOPS, proposal))
    ranked = sorted(scored.values(), key=lambda item: (item[0], item[1].transfer_station))
    return [proposal for _, proposal in ranked[:limit]]


def check(proposal: TransferProposal, facts: TransferFacts) -> tuple[RejectionCode, str] | None:
    """Why a proposal cannot be right for these facts, or None if it may be."""
    option = facts.option(proposal.transfer_station)
    if option is None:
        return "unknown_id", f"{proposal.transfer_station} is not a candidate transfer station"
    reach = {line.route_id for line in option.to_transfer}
    onward = {line.route_id for line in option.to_destination}
    if missing := set(proposal.first_routes) - reach:
        return "not_served", f"{sorted(missing)} do not run to {option.name}"
    if missing := set(proposal.second_routes) - onward:
        return "not_served", f"{sorted(missing)} do not run from {option.name} to the destination"
    if set(proposal.first_routes) & set(proposal.second_routes):
        return "not_served", "the same line on both sides is not a transfer"
    segment = facts.segment
    if proposal.transfer_station in segment.transfer_stations and set(segment.route_ids) <= (
        set(proposal.first_routes) | set(proposal.second_routes)
    ):
        return "same_as_baseline", "the baseline route already makes this transfer"
    return None


def _names(facts: TransferFacts, routes: set[str], station: str, first: bool) -> str:
    option = facts.option(station)
    lines = (option.to_transfer if first else option.to_destination) if option else ()
    names = sorted({line.route for line in lines if line.route_id in routes})
    return "/".join(names) or "/".join(sorted(routes))
