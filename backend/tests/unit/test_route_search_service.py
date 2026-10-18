import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.cache.redis import ResilientRedis
from app.core.config import Settings
from app.core.errors import RoutingUnavailableError
from app.core.graph_version import GraphVersion
from app.core.resilience import CircuitBreaker
from app.schemas.routes import AccessibilityRequirements, Route
from app.services.accessibility.snapshot import (
    AccessibilitySnapshot,
    Elevator,
    SnapshotProvider,
)
from app.services.route_search import CacheStatus, RouteSearchService
from tests.fakes import (
    FakeOtpServer,
    StaticSnapshots,
    fake_otp,
    in_memory_redis,
    make_request,
    plan_fixture,
)
from tests.unit.test_redis_resilience import FailingRedis

DEPART = datetime.fromisoformat("2026-10-19T08:30:00-04:00")


def service(
    server: FakeOtpServer,
    redis: ResilientRedis | None = None,
    graph_file: Path | None = None,
    snapshots: SnapshotProvider | None = None,
) -> RouteSearchService:
    settings = Settings(_env_file=None, app_env="test")
    return RouteSearchService(
        fake_otp(server.handler),
        redis or in_memory_redis(),
        GraphVersion(graph_file),
        snapshots or StaticSnapshots(),
        settings,
    )


async def test_miss_then_hit() -> None:
    server = FakeOtpServer()
    svc = service(server)
    first = await svc.search(make_request(DEPART))
    second = await svc.search(make_request(DEPART))

    assert (first.cache, second.cache) == (CacheStatus.MISS, CacheStatus.HIT)
    assert server.calls == 1
    assert first.response.routes == second.response.routes
    assert first.response.search_id != second.response.search_id


async def test_concurrent_identical_searches_query_otp_once() -> None:
    server = FakeOtpServer(delay_s=0.05)
    svc = service(server)
    outcomes = await asyncio.gather(*(svc.search(make_request(DEPART)) for _ in range(5)))

    assert server.calls == 1
    assert sorted(o.cache for o in outcomes) == sorted(
        [CacheStatus.MISS] + [CacheStatus.SHARED] * 4
    )


async def test_different_accessibility_needs_never_share_results() -> None:
    server = FakeOtpServer()
    svc = service(server)
    step_free = AccessibilityRequirements(step_free_required=True)
    await svc.search(make_request(DEPART))
    await svc.search(make_request(DEPART, accessibility=step_free))

    assert server.calls == 2
    wheelchair_flags = [
        (r["variables"]["preferences"] or {}).get("accessibility") for r in server.requests
    ]
    assert wheelchair_flags == [None, {"wheelchair": {"enabled": True}}]


async def test_errors_are_not_cached() -> None:
    server = FakeOtpServer(failures=1)
    svc = service(server)
    with pytest.raises(RoutingUnavailableError):
        await svc.search(make_request(DEPART))

    outcome = await svc.search(make_request(DEPART))
    assert outcome.cache == CacheStatus.MISS
    assert server.calls == 2


async def test_drops_routes_leaving_before_requested_time() -> None:
    svc = service(FakeOtpServer())
    all_routes = (await svc.search(make_request(DEPART))).response.routes
    first, second = all_routes[0].departure, all_routes[1].departure

    # OTP is queried from the start of the 5-minute bucket, so results can
    # include trains that leave before the rider asked; they must be dropped.
    requested = second - timedelta(seconds=30)
    later = await svc.search(make_request(requested))
    departures = [r.departure for r in later.response.routes]
    assert first not in departures
    assert departures and min(departures) >= requested - timedelta(minutes=1)


async def test_walk_limit_filters_and_explains() -> None:
    svc = service(FakeOtpServer())
    tight = AccessibilityRequirements(max_walk_meters=100)
    outcome = await svc.search(make_request(DEPART, accessibility=tight))

    assert outcome.response.routes == []
    assert [n.code for n in outcome.response.notices] == ["WALK_LIMIT_EXCEEDED"]


async def test_routing_errors_become_notices() -> None:
    server = FakeOtpServer()
    server.payload = {
        "data": {
            "planConnection": {
                "edges": [],
                "routingErrors": [{"code": "OUTSIDE_SERVICE_PERIOD", "description": "raw"}],
            }
        }
    }
    outcome = await service(server).search(make_request(DEPART))
    assert outcome.response.routes == []
    assert outcome.response.notices[0].code == "OUTSIDE_SERVICE_PERIOD"
    assert outcome.response.notices[0].message != "raw"


async def test_duplicate_routing_errors_are_reported_once() -> None:
    server = FakeOtpServer()
    error = {"code": "NO_STOPS_IN_RANGE", "description": "raw"}
    server.payload = {
        "data": {"planConnection": {"edges": [], "routingErrors": [error, dict(error)]}}
    }
    outcome = await service(server).search(make_request(DEPART))
    assert [n.code for n in outcome.response.notices] == ["NO_STOPS_IN_RANGE"]


async def test_works_without_redis() -> None:
    server = FakeOtpServer()
    broken = ResilientRedis(FailingRedis(), CircuitBreaker("redis", 1, 60))  # type: ignore[arg-type]
    svc = service(server, redis=broken)
    for _ in range(2):
        outcome = await svc.search(make_request(DEPART))
        assert outcome.cache == CacheStatus.MISS
        assert outcome.response.routes
    assert server.calls == 2


async def test_new_graph_version_invalidates_cache(tmp_path: Path) -> None:
    graph_file = tmp_path / "graph-version"
    graph_file.write_text("build-1")
    server = FakeOtpServer()
    svc = service(server, graph_file=graph_file)
    await svc.search(make_request(DEPART))
    await svc.search(make_request(DEPART))
    assert server.calls == 1

    graph_file.write_text("build-2")
    outcome = await svc.search(make_request(DEPART))
    assert outcome.cache == CacheStatus.MISS
    assert outcome.response.graph_version == "build-2"
    assert server.calls == 2


def test_fixture_has_routes() -> None:
    assert plan_fixture()["data"]["planConnection"]["edges"]


def all_elevators(working: bool) -> AccessibilitySnapshot:
    """Every station in the fixture has one ADA elevator, working or not."""
    stations = {
        place["stop"]["parentStation"]["gtfsId"].split(":")[1]
        for edge in plan_fixture()["data"]["planConnection"]["edges"]
        for leg in edge["node"]["legs"]
        for place in (leg["from"], leg["to"])
        if place.get("stop") and place["stop"].get("parentStation")
    }
    elevator = Elevator("EL1", True, working, None, None if working else "Repair", None)
    return AccessibilitySnapshot(
        elevators_by_stop={s: [elevator] for s in stations},
        observed_at=datetime.now(UTC),
    )


def station_statuses(routes: list[Route]) -> set[str]:
    return {
        f.status for r in routes for f in r.accessibility_findings if f.source == "mta_elevators"
    }


async def test_cached_routes_are_reassessed_on_every_request() -> None:
    """An elevator breaking after a route was cached must change its verdict."""
    snapshots = StaticSnapshots(all_elevators(working=True))
    svc = service(FakeOtpServer(), snapshots=snapshots)
    first = await svc.search(make_request(DEPART))
    snapshots.snapshot = all_elevators(working=False)
    second = await svc.search(make_request(DEPART))

    assert second.cache == CacheStatus.HIT
    before = [
        f.status
        for r in first.response.routes
        for f in r.accessibility_findings
        if f.source == "mta_elevators"
    ]
    after = [
        f.status
        for r in second.response.routes
        for f in r.accessibility_findings
        if f.source == "mta_elevators"
    ]
    assert set(before) == {"meets"}
    assert set(after) == {"violates"}


async def test_required_access_separates_unconfirmed_routes() -> None:
    svc = service(FakeOtpServer(), snapshots=StaticSnapshots(all_elevators(working=True)))
    wheelchair = AccessibilityRequirements(wheelchair_required=True)
    outcome = await svc.search(make_request(DEPART, accessibility=wheelchair))

    response = outcome.response
    assert response.routes == []
    assert response.review_only_routes
    # The recorded walk includes mapped stairs, so none can be confirmed.
    assert {r.accessibility_status for r in response.review_only_routes} <= {"violates", "unknown"}
    assert "NO_CONFIRMED_ACCESSIBLE_ROUTE" in [n.code for n in response.notices]


async def test_without_requirements_all_routes_are_shown_with_verdicts() -> None:
    outcome = await service(FakeOtpServer()).search(make_request(DEPART))
    assert outcome.response.routes
    assert outcome.response.review_only_routes == []
    assert all(r.accessibility_findings for r in outcome.response.routes)
    assert outcome.response.data_freshness[0].source == "mta_elevators"
