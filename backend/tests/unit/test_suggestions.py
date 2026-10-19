import json
from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.ai.guard import GuardedLlm, UnlimitedBudget
from app.ai.provider import LlmResponse
from app.ai.suggestions import ModelProposalGenerator, facts_document
from app.core.resilience import CircuitBreaker
from app.integrations.otp.mapper import to_route
from app.schemas.routes import AccessibilityRequirements, Route
from app.schemas.suggestions import (
    LaterTransfer,
    RejectedProposal,
    TransferProposal,
    VerifiedSuggestion,
)
from app.services.accessibility.snapshot import AccessibilitySnapshot
from app.services.suggestions import rules
from app.services.suggestions.context import (
    SubwaySegment,
    TransferFacts,
    build_facts,
    subway_segment,
)
from app.services.suggestions.service import SuggestionService
from app.services.suggestions.verifier import VerificationInput, follows, verify
from app.services.transit.alerts import ActiveAlert, AlertSnapshot
from tests.fakes import StaticSnapshots, fake_otp, make_request, plan_fixture
from tests.suggestion_fakes import (
    EXPRESS,
    LOCAL,
    ONE,
    P1,
    P2,
    P4,
    P6,
    P7,
    T0,
    TWO,
    A,
    B,
    C,
    D,
    E,
    FakeKnowledge,
    itinerary,
    plan_response,
)

EXPRESS_THEN_LOCAL = TransferProposal(
    first_routes=[EXPRESS], transfer_station=C, second_routes=[LOCAL]
)
THERE_AND_BACK = TransferProposal(
    first_routes=[ONE],
    transfer_station=P2,
    second_routes=[TWO],
    then=[LaterTransfer(station=P6, routes=[ONE])],
)


def baseline() -> Route:
    return to_route(itinerary([(LOCAL, A, D, 2, 14)]))


def faster() -> dict[str, Any]:
    return itinerary([(EXPRESS, A, C, 1, 5), (LOCAL, C, D, 7, 10)])


async def facts_for(route: Route) -> TransferFacts:
    segment = subway_segment(route)
    assert segment is not None
    facts = await build_facts(segment, FakeKnowledge(), AlertSnapshot.build([]))
    assert facts is not None
    return facts


class StaticAlerts:
    def __init__(self, *alerts: ActiveAlert) -> None:
        self.snapshot = AlertSnapshot.build(list(alerts))

    async def current(self) -> AlertSnapshot:
        return self.snapshot


class OtpPlans:
    def __init__(self, *itineraries: dict[str, Any]) -> None:
        self.payload = plan_response(*itineraries)
        self.variables: list[dict[str, Any]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.variables.append(json.loads(request.content)["variables"])
        return httpx.Response(200, json=self.payload)


def suspension(route_id: str) -> ActiveAlert:
    return ActiveAlert(
        alert_id="a1",
        alert_type="Part Suspended",
        header="No [X] trains between A St and C St.",
        periods=((0, None),),
        route_ids=frozenset({route_id}),
        station_ids=frozenset(),
    )


# --- segment and facts -------------------------------------------------------


def test_segment_of_a_path_and_subway_journey() -> None:
    route = to_route(plan_fixture()["data"]["planConnection"]["edges"][0]["node"])
    segment = subway_segment(route)
    assert segment is not None
    assert segment.board_station == "MTASBWY:138"
    assert segment.alight_station == "MTASBWY:117"
    assert segment.route_ids == ("MTASBWY:1",)
    assert segment.other_route_ids == ("PATH:RED",)


def test_no_segment_without_subway_or_with_a_gap() -> None:
    bus = to_route(itinerary([("MTABC:Q32", A, B, 1, 9)]))
    split = to_route(
        itinerary([(LOCAL, A, B, 1, 3), ("MTABC:Q32", B, C, 4, 9), (EXPRESS, C, E, 10, 14)])
    )
    assert subway_segment(bus) is None
    assert subway_segment(split) is None


def detour_baseline() -> Route:
    return to_route(itinerary([(ONE, P1, P7, 2, 20)]))


async def test_facts_keep_only_stations_where_lines_meet() -> None:
    facts = await facts_for(baseline())
    # B is served by the local only; E is past D.
    assert facts.interchanges == (C,)
    to_c = facts.lines(A, C)
    assert [line.route_id for line in to_c] == [EXPRESS, LOCAL]
    assert to_c[0].stops == 1 and to_c[1].stops == 2
    assert [line.route_id for line in facts.lines(C, D)] == [LOCAL]
    assert facts.lines(C, D)[0].headway_s == 360
    assert [line.route_id for line in facts.direct] == [LOCAL]


async def test_facts_include_where_an_express_meets_the_local_again() -> None:
    facts = await facts_for(detour_baseline())
    # P4 is an express stop too; P6 is the last before the express leaves.
    assert facts.interchanges == (P2, P4, P6)
    assert [line.route_id for line in facts.lines(P2, P6)] == [TWO, ONE]
    assert facts.lines(P2, P6)[0].stops == 2
    assert TWO not in {line.route_id for line in facts.lines(P2, P7)}  # the express leaves first
    assert [line.route_id for line in facts.lines(P6, P7)] == [ONE]


async def test_fact_document_exposes_only_timetable_ids() -> None:
    document = facts_document(await facts_for(baseline()))
    text = json.dumps(document)
    assert document["board"] == {"station_id": A, "name": "A St"}
    assert [s["station_id"] for s in document["stations"]] == [A, C, D]
    ride = next(r for r in document["rides"] if (r["from"], r["to"]) == (C, D))
    assert ride["lines"][0]["minutes_between_trains"] == 6.0
    assert "lat" not in text and "Destination" not in text


# --- rules --------------------------------------------------------------------


async def test_rules_propose_express_then_local() -> None:
    proposals = rules.propose(await facts_for(baseline()))
    assert proposals[0] == EXPRESS_THEN_LOCAL.model_copy(
        update={"reason": "Ride the X to C St, then the L to D St."}
    )


async def test_rules_propose_a_change_to_the_express_and_back() -> None:
    proposals = rules.propose(await facts_for(detour_baseline()), limit=10)
    assert THERE_AND_BACK.signature() in {p.signature() for p in proposals}
    plan = next(p for p in proposals if p.signature() == THERE_AND_BACK.signature())
    assert plan.reason == "Ride the 1 to P2 St, then the 2 to P6 St, then the 1 to P7 St."


async def test_rules_respect_the_transfer_limit() -> None:
    proposals = rules.propose(await facts_for(detour_baseline()), limit=10, max_transfers=1)
    assert proposals == []  # the express never reaches P7, so one change is not enough


@pytest.mark.parametrize(
    ("first", "station", "second", "code"),
    [
        ([EXPRESS], B, [LOCAL], "unknown_id"),
        (["MTASBWY:Q"], C, [LOCAL], "not_served"),
        ([EXPRESS], C, [EXPRESS], "not_served"),
        ([LOCAL], C, [LOCAL], "not_served"),
    ],
)
async def test_check_rejects_impossible_proposals(
    first: list[str], station: str, second: list[str], code: str
) -> None:
    proposal = TransferProposal(first_routes=first, transfer_station=station, second_routes=second)
    problem = rules.check(proposal, await facts_for(baseline()))
    assert problem is not None and problem[0] == code


@pytest.mark.parametrize(
    ("proposal", "code"),
    [
        # Back to the 1 before changing to the 2: stations out of riding order.
        (
            THERE_AND_BACK.model_copy(
                update={"transfer_station": P6, "then": [LaterTransfer(station=P2, routes=[ONE])]}
            ),
            "not_served",
        ),
        # The express does not reach P7, so the plan must change back.
        (
            TransferProposal(first_routes=[ONE], transfer_station=P2, second_routes=[TWO]),
            "not_served",
        ),
        (
            THERE_AND_BACK.model_copy(update={"then": [LaterTransfer(station=P7, routes=[ONE])]}),
            "unknown_id",
        ),
    ],
)
async def test_check_rejects_impossible_chains(proposal: TransferProposal, code: str) -> None:
    problem = rules.check(proposal, await facts_for(detour_baseline()))
    assert problem is not None and problem[0] == code


async def test_check_accepts_a_chain_the_timetable_allows() -> None:
    assert rules.check(THERE_AND_BACK, await facts_for(detour_baseline())) is None
    via_p4 = THERE_AND_BACK.model_copy(update={"then": [LaterTransfer(station=P4, routes=[ONE])]})
    assert rules.check(via_p4, await facts_for(detour_baseline())) is None


async def test_check_rejects_the_baseline_transfer() -> None:
    route = to_route(itinerary([(EXPRESS, A, C, 1, 5), (LOCAL, C, D, 7, 10)]))
    facts = await facts_for(baseline())
    segment = subway_segment(route)
    assert segment is not None
    same = TransferFacts(
        segment=segment,
        board_name=facts.board_name,
        alight_name=facts.alight_name,
        direct=facts.direct,
        interchanges=facts.interchanges,
        names=facts.names,
        rides=facts.rides,
    )
    assert rules.check(EXPRESS_THEN_LOCAL, same) == (
        "same_as_baseline",
        "the baseline route already makes this transfer",
    )


# --- verification ------------------------------------------------------------


def verification(
    segment: SubwaySegment,
    baseline_arrival_min: float = 17,
    alerts: AlertSnapshot | None = None,
    accessibility: AccessibilityRequirements | None = None,
) -> VerificationInput:
    return VerificationInput(
        request=make_request(depart_at=T0, accessibility=accessibility),
        requested=T0,
        segment=segment,
        baseline_arrival=T0 + timedelta(minutes=baseline_arrival_min),
        accessibility=AccessibilitySnapshot(),
        alerts=alerts or AlertSnapshot.build([]),
        now=T0,
    )


def segment() -> SubwaySegment:
    found = subway_segment(baseline())
    assert found is not None
    return found


def test_follows_needs_the_named_lines_in_and_out_of_the_station() -> None:
    assert follows(to_route(faster()), EXPRESS_THEN_LOCAL)
    # Ending a ride at C satisfies OTP's "visit" but is not a transfer.
    assert not follows(to_route(itinerary([(EXPRESS, A, C, 1, 5)])), EXPRESS_THEN_LOCAL)
    # Changing between two trains of the same line is not the proposal either.
    same_line = itinerary([(LOCAL, A, C, 1, 6), (LOCAL, C, D, 7, 10)])
    assert not follows(to_route(same_line), EXPRESS_THEN_LOCAL)


def test_follows_every_ride_of_a_chain() -> None:
    detour = itinerary([(ONE, P1, P2, 1, 3), (TWO, P2, P6, 4, 9), (ONE, P6, P7, 11, 13)])
    assert follows(to_route(detour), THERE_AND_BACK)
    # Staying on the express past P6 is not the plan.
    skipped = itinerary([(ONE, P1, P2, 1, 3), (TWO, P2, P6, 4, 9)])
    assert not follows(to_route(skipped), THERE_AND_BACK)


async def test_a_route_that_reaches_the_first_transfer_another_way_counts() -> None:
    # OTP may skip the plan's first ride when another allowed line (PATH) gets
    # the rider to the first transfer station: that is still the shortcut.
    shortcut = itinerary([(TWO, P2, P6, 4, 9), (ONE, P6, P7, 11, 13)])
    otp = OtpPlans(shortcut)
    found = subway_segment(detour_baseline())
    assert found is not None
    outcome = await verify(fake_otp(otp.handler), THERE_AND_BACK, "rules", verification(found, 23))
    assert isinstance(outcome, VerifiedSuggestion)
    assert outcome.proposal.stations == [P6]
    assert outcome.proposal.rides == [[TWO], [ONE]]
    # The fake itinerary names each place by its station ID.
    assert outcome.proposal.reason == f"Ride the 2 from {P2} to {P6}, then the 1 to {P7}."


async def test_a_chain_is_verified_through_every_transfer_station() -> None:
    detour = itinerary([(ONE, P1, P2, 1, 3), (TWO, P2, P6, 4, 9), (ONE, P6, P7, 11, 13)])
    otp = OtpPlans(detour)
    found = subway_segment(detour_baseline())
    assert found is not None
    outcome = await verify(fake_otp(otp.handler), THERE_AND_BACK, "model", verification(found, 23))
    assert isinstance(outcome, VerifiedSuggestion)
    assert outcome.saves_seconds == 420
    assert otp.variables[0]["via"] == [
        {"visit": {"stopLocationIds": [P2]}},
        {"visit": {"stopLocationIds": [P6]}},
    ]
    assert otp.variables[0]["preferences"]["transit"]["filters"] == [
        {"include": [{"routes": [ONE, TWO]}]}
    ]


async def test_verified_suggestion_is_a_complete_otp_itinerary() -> None:
    otp = OtpPlans(faster())
    outcome = await verify(
        fake_otp(otp.handler), EXPRESS_THEN_LOCAL, "rules", verification(segment())
    )

    assert isinstance(outcome, VerifiedSuggestion)
    assert outcome.saves_seconds == 240
    assert outcome.route.origin_type == "rules_suggestion"
    assert outcome.route.accessibility_findings  # assessed like any route
    variables = otp.variables[0]
    assert variables["via"] == [{"visit": {"stopLocationIds": [C]}}]
    assert variables["preferences"]["transit"]["filters"] == [
        {"include": [{"routes": [EXPRESS, LOCAL]}]}
    ]


async def test_other_lines_of_the_journey_stay_allowed() -> None:
    route = to_route(plan_fixture()["data"]["planConnection"]["edges"][0]["node"])
    found = subway_segment(route)
    assert found is not None
    otp = OtpPlans(faster())
    await verify(fake_otp(otp.handler), EXPRESS_THEN_LOCAL, "rules", verification(found))
    routes = otp.variables[0]["preferences"]["transit"]["filters"][0]["include"][0]["routes"]
    assert routes == ["PATH:RED", EXPRESS, LOCAL]


@pytest.mark.parametrize(
    ("itineraries", "baseline_arrival", "code"),
    [
        ([], 17, "no_itinerary"),
        ([itinerary([(EXPRESS, A, C, 1, 5)])], 17, "no_itinerary"),
        ([faster()], 13.5, "not_faster"),
        ([faster()], 12, "not_faster"),
    ],
)
async def test_rejections(
    itineraries: list[dict[str, Any]], baseline_arrival: float, code: str
) -> None:
    otp = OtpPlans(*itineraries)
    outcome = await verify(
        fake_otp(otp.handler),
        EXPRESS_THEN_LOCAL,
        "model",
        verification(segment(), baseline_arrival),
    )
    assert isinstance(outcome, RejectedProposal) and outcome.code == code
    assert outcome.source == "model"


async def test_departure_before_the_requested_time_is_not_used() -> None:
    early = itinerary([(EXPRESS, A, C, -5, -1), (LOCAL, C, D, 1, 4)])
    outcome = await verify(
        fake_otp(OtpPlans(early).handler), EXPRESS_THEN_LOCAL, "rules", verification(segment())
    )
    assert isinstance(outcome, RejectedProposal) and outcome.code == "no_itinerary"


async def test_suspended_line_is_rejected_without_asking_otp() -> None:
    otp = OtpPlans(faster())
    outcome = await verify(
        fake_otp(otp.handler),
        EXPRESS_THEN_LOCAL,
        "rules",
        verification(segment(), alerts=AlertSnapshot.build([suspension(EXPRESS)])),
    )
    assert isinstance(outcome, RejectedProposal) and outcome.code == "line_suspended"
    assert otp.variables == []


async def test_unconfirmed_accessibility_is_rejected_when_required() -> None:
    outcome = await verify(
        fake_otp(OtpPlans(faster()).handler),
        EXPRESS_THEN_LOCAL,
        "rules",
        verification(segment(), accessibility=AccessibilityRequirements(step_free_required=True)),
    )
    assert isinstance(outcome, RejectedProposal) and outcome.code == "accessibility"
    assert outcome.detail == "accessibility is unknown"


# --- the whole investigation -------------------------------------------------


class ScriptedProvider:
    name = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def complete_json(self, system: str, user: str, max_tokens: int) -> LlmResponse:
        self.prompts.append(user)
        return LlmResponse(text=self.reply, model="scripted-1")


def service(otp: OtpPlans, provider: ScriptedProvider | None = None) -> SuggestionService:
    model = None
    if provider is not None:
        llm = GuardedLlm(provider, UnlimitedBudget(), CircuitBreaker("llm", 5, 10), timeout_s=5)
        model = ModelProposalGenerator(llm)
    return SuggestionService(
        fake_otp(otp.handler), FakeKnowledge(), StaticAlerts(), StaticSnapshots(), model
    )


async def test_rules_only_investigation() -> None:
    result = await service(OtpPlans(faster())).investigate(make_request(depart_at=T0), [baseline()])
    assert result.applicable and result.generator == "rules"
    assert [s.source for s in result.suggestions] == ["rules"]


async def test_model_proposals_are_checked_and_deduplicated() -> None:
    reply = json.dumps(
        {
            "proposals": [
                {
                    "first_routes": [EXPRESS],
                    "transfer_station": C,
                    "second_routes": [LOCAL],
                    "reason": "The express skips B.",
                },
                {
                    "first_routes": [EXPRESS],
                    "transfer_station": "MTASBWY:Q99",
                    "second_routes": [LOCAL],
                    "reason": "Invented station.",
                },
            ]
        }
    )
    provider = ScriptedProvider(reply)
    result = await service(OtpPlans(faster()), provider).investigate(
        make_request(depart_at=T0), [baseline()]
    )

    assert result.generator == "scripted+rules"
    assert [(s.source, s.proposal.reason) for s in result.suggestions] == [
        ("model", "The express skips B.")
    ]
    assert [(r.source, r.code) for r in result.rejected] == [("model", "unknown_id")]
    assert result.suggestions[0].route.origin_type == "model_suggestion"
    assert "<facts>" in provider.prompts[0]


@pytest.mark.parametrize("reply", ["not json", '{"proposals": [{"first_routes": "X"}]}'])
async def test_invalid_model_reply_falls_back_to_rules(reply: str) -> None:
    result = await service(OtpPlans(faster()), ScriptedProvider(reply)).investigate(
        make_request(depart_at=T0), [baseline()]
    )
    assert [s.source for s in result.suggestions] == ["rules"]


async def test_fewer_transfers_skips_the_investigation() -> None:
    otp = OtpPlans(faster())
    request = make_request(depart_at=T0).model_copy(
        update={
            "optimization": make_request().optimization.model_copy(
                update={"prefer_fewer_transfers": True}
            )
        }
    )
    result = await service(otp).investigate(request, [baseline()])
    assert not result.applicable and result.reason == "Fewer transfers is selected."
    assert otp.variables == []


async def test_not_applicable_without_a_subway_segment() -> None:
    bus = to_route(itinerary([("MTABC:Q32", A, B, 1, 9)]))
    result = await service(OtpPlans()).investigate(make_request(depart_at=T0), [bus])
    assert not result.applicable
    assert result.reason == "The fastest route has no single subway segment."


@pytest.mark.parametrize(
    ("alert_type", "suspends"),
    [
        ("Suspended", True),
        ("Part Suspended", True),
        ("Planned - Part Suspended", False),  # already in the supplemented timetable
        ("Delays", False),
        (None, False),
    ],
)
def test_only_unplanned_suspensions_block_a_line(alert_type: str | None, suspends: bool) -> None:
    alert = suspension(EXPRESS)
    assert ActiveAlert(**{**alert.__dict__, "alert_type": alert_type}).suspends_service is suspends
