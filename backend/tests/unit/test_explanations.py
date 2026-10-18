import json
from datetime import timedelta

import pytest
from evals.explanation_scenario import NOW, scenario_facts
from evals.explanations import CASES_FILE

from app.ai.explanations import check_grounding, parse_explanation
from app.ai.guard import GuardedLlm, UnlimitedBudget
from app.ai.provider import DisabledProvider, LlmResponse
from app.core.errors import NotFoundError
from app.core.resilience import CircuitBreaker
from app.integrations.otp.mapper import to_route
from app.schemas.routes import Route, RouteSearchResponse
from app.services.accessibility.assessor import assess_route
from app.services.accessibility.snapshot import AccessibilitySnapshot, Elevator
from app.services.explanations.facts import route_facts
from app.services.explanations.service import ExplanationService
from app.services.search_results import SearchResults
from app.services.transit.alerts import ActiveAlert, AlertSnapshot
from tests.fakes import StaticSnapshots, in_memory_redis, make_request, plan_fixture
from tests.unit.test_suggestions import StaticAlerts

SEARCH_ID = "a" * 32
FAITHFUL: str = next(
    case["reply"]
    for case in map(json.loads, CASES_FILE.read_text().splitlines())
    if case["id"] == "faithful_full"
)


def fixture_route() -> Route:
    return to_route(plan_fixture()["data"]["planConnection"]["edges"][0]["node"])


# --- facts --------------------------------------------------------------------


def test_facts_cover_every_leg_transfer_and_finding() -> None:
    facts = {f.id: f for f in scenario_facts()}
    assert facts["summary"].text.startswith("Leaves at 08:34 and arrives at 09:33")
    assert "from your starting point to Journal Square" in facts["leg0"].text
    assert facts["transfer3"].text.endswith("with 10 min between trains.")
    assert {k for k, f in facts.items() if f.required} == {
        "accessibility",
        "finding0",
        "finding1",
        "finding2",
        "finding5",
        "alert0",
    }
    assert "MTA elevator status as of 08:20" in facts["finding5"].text


def test_stale_elevator_data_is_a_required_fact() -> None:
    route = fixture_route()
    stale = AccessibilitySnapshot(
        elevators_by_stop={"138": [Elevator("EL1", True, True, None, None, None)]},
        observed_at=NOW - timedelta(minutes=45),
    )
    facts = route_facts(assess_route(route, stale, True, NOW), True, stale, AlertSnapshot(), NOW)
    data = next(f for f in facts if f.id == "elevator_data")
    assert data.required and "expired" in data.text


def test_meeting_routes_do_not_require_mentioning_accessibility_unless_asked() -> None:
    route = fixture_route().model_copy(update={"accessibility_findings": []})
    facts = route_facts(route, False, AccessibilitySnapshot(), AlertSnapshot(), NOW)
    assert not any(f.required for f in facts)


# --- grounding ----------------------------------------------------------------


def test_sentence_starters_are_not_treated_as_names() -> None:
    facts = scenario_facts()
    reply = json.loads(FAITHFUL)
    first = reply["sentences"][0]["text"]
    reply["sentences"][0]["text"] = "Then " + first[0].lower() + first[1:]
    assert check_grounding(parse_explanation(json.dumps(reply)), facts) == []


def test_a_capitalised_unknown_word_at_the_start_is_still_checked() -> None:
    facts = scenario_facts()
    reply = json.loads(FAITHFUL)
    reply["sentences"][0]["text"] = "Grand Central is on the way."
    problems = check_grounding(parse_explanation(json.dumps(reply)), facts)
    assert any("Central" in p.message and "Grand" in p.message for p in problems)


# --- service ------------------------------------------------------------------


class Scripted:
    name = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def complete_json(self, system: str, user: str, max_tokens: int) -> LlmResponse:
        self.calls += 1
        assert "<facts>" in user and "finding5" in user
        return LlmResponse(text=self.reply, model="scripted-1")


async def service(
    provider: Scripted | DisabledProvider | None = None,
) -> ExplanationService:
    redis = in_memory_redis()
    results = SearchResults(redis, ttl_s=60)
    await results.save(
        RouteSearchResponse(
            search_id=SEARCH_ID, graph_version="g", routes=[fixture_route()], notices=[]
        ),
        make_request(),
    )
    llm = (
        GuardedLlm(provider, UnlimitedBudget(), CircuitBreaker("llm", 5, 10), timeout_s=5)
        if provider is not None
        else None
    )
    snapshot = AccessibilitySnapshot(
        elevators_by_stop={"138": [Elevator("EL1", True, True, None, None, None)]},
        observed_at=None,  # never loaded: stations read as unknown
    )
    return ExplanationService(
        results, StaticSnapshots(snapshot), StaticAlerts(), llm, redis, b"s", 60
    )


async def test_without_a_model_the_facts_are_the_explanation() -> None:
    explanation = await (await service()).explain(SEARCH_ID, fixture_route().route_id)
    assert explanation.source == "facts" and explanation.notice is None
    assert [s.fact_ids for s in explanation.sentences] == [[f.id] for f in explanation.facts]


async def test_route_is_reassessed_with_current_data() -> None:
    explanation = await (await service()).explain(SEARCH_ID, fixture_route().route_id)
    texts = " ".join(f.text for f in explanation.facts)
    # The stored route had no findings; the explanation assessed it now.
    assert "Elevator data is not available right now." in texts


async def test_unknown_route_or_search_is_not_found() -> None:
    svc = await service()
    with pytest.raises(NotFoundError):
        await svc.explain(SEARCH_ID, "f" * 16)
    with pytest.raises(NotFoundError):
        await svc.explain("b" * 32, fixture_route().route_id)


async def test_ungrounded_model_wording_falls_back_to_facts_with_a_notice() -> None:
    reply = json.dumps(
        {"sentences": [{"text": "The route is step-free.", "fact_ids": ["summary"]}]}
    )
    explanation = await (await service(Scripted(reply))).explain(
        SEARCH_ID, fixture_route().route_id
    )
    assert explanation.source == "facts"
    assert explanation.notice is not None and "did not match" in explanation.notice


async def test_unavailable_model_falls_back_silently() -> None:
    explanation = await (await service(DisabledProvider())).explain(
        SEARCH_ID, fixture_route().route_id
    )
    assert explanation.source == "facts" and explanation.notice is None


async def test_grounded_model_wording_is_used_and_cached() -> None:
    svc = await service()
    facts = (await svc.explain(SEARCH_ID, fixture_route().route_id)).facts
    # Word every fact faithfully by citing it with its own text.
    reply = json.dumps({"sentences": [{"text": f.text, "fact_ids": [f.id]} for f in facts]})
    provider = Scripted(reply)
    svc = await service(provider)
    first = await svc.explain(SEARCH_ID, fixture_route().route_id)
    second = await svc.explain(SEARCH_ID, fixture_route().route_id)
    assert (first.source, first.cached, second.cached) == ("model", False, True)
    assert first.provider == "scripted" and provider.calls == 1


def test_alerts_are_those_in_effect_during_the_trip() -> None:
    route = fixture_route()  # rides the 1 from 09:00 on 2026-10-19
    ride = next(leg for leg in route.legs if leg.route_id == "MTASBWY:1").start_time
    tonight_only = ActiveAlert(
        alert_id="later",
        alert_type="Planned - Part Suspended",
        header="No [1] trains overnight.",
        periods=((int((ride + timedelta(hours=12)).timestamp()), None),),
        route_ids=frozenset({"MTASBWY:1"}),
        station_ids=frozenset(),
    )
    facts = route_facts(
        route, False, AccessibilitySnapshot(), AlertSnapshot.build([tonight_only]), NOW
    )
    assert not [f for f in facts if f.kind == "alert"]


def test_trips_later_on_say_that_elevator_status_is_current_not_forecast() -> None:
    route = fixture_route()
    snapshot = AccessibilitySnapshot(
        elevators_by_stop={"138": [Elevator("EL1", True, True, None, None, None)]},
        observed_at=NOW,
    )
    assessed = assess_route(route, snapshot, True, NOW)
    soon = route_facts(assessed, True, snapshot, AlertSnapshot(), NOW)
    later = route_facts(assessed, True, snapshot, AlertSnapshot(), NOW - timedelta(days=1))
    assert "status_now" not in {f.id for f in soon}
    assert "it can change before you travel at 08:34" in {f.id: f.text for f in later}["status_now"]
