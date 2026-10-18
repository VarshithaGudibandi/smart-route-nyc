from app.integrations.otp.mapper import to_route
from app.schemas.routes import InvestigationRef, Route, RouteSearchResponse
from app.services.search_results import SearchResults
from tests.fakes import in_memory_redis, make_request, plan_fixture
from tests.suggestion_fakes import EXPRESS, LOCAL, C, itinerary

SEARCH_ID = "a" * 32


def baseline() -> Route:
    return to_route(plan_fixture()["data"]["planConnection"]["edges"][0]["node"])


def response(investigation: InvestigationRef | None = None) -> RouteSearchResponse:
    return RouteSearchResponse(
        search_id=SEARCH_ID,
        graph_version="g1",
        routes=[baseline()],
        notices=[],
        investigation=investigation,
    )


async def test_routes_of_a_stored_search_are_found_by_id() -> None:
    results = SearchResults(in_memory_redis(), ttl_s=60)
    await results.save(response(), make_request())
    found = await results.route(SEARCH_ID, baseline().route_id)
    assert found is not None and found[1] == baseline()
    assert await results.route(SEARCH_ID, "f" * 16) is None
    assert await results.route("b" * 32, baseline().route_id) is None


async def test_suggestion_routes_are_found_through_the_investigation() -> None:
    suggestion = to_route(
        itinerary([(EXPRESS, "MTASBWY:A", C, 1, 5), (LOCAL, C, "MTASBWY:D", 7, 10)])
    )
    asked: list[str] = []

    async def suggestions(investigation_id: str) -> list[Route]:
        asked.append(investigation_id)
        return [suggestion]

    results = SearchResults(in_memory_redis(), ttl_s=60, suggestions=suggestions)
    await results.save(
        response(InvestigationRef(id="c" * 24, status="queued", poll_after_ms=1)), make_request()
    )
    found = await results.route(SEARCH_ID, suggestion.route_id)
    assert found is not None and found[1] == suggestion and asked == ["c" * 24]
