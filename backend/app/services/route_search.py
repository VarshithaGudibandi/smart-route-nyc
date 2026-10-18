"""Baseline route search: OTP itineraries behind a cache and request coalescing.

Flow for one request:
1. Bucket the departure time and build the cache key.
2. Cache hit: reuse the stored itineraries.
3. Miss: coalesce with identical in-flight requests in this process
   (``SingleFlight``) and across instances (a Redis lock); the leader queries
   OTP and stores successful results.
4. Always: drop itineraries that leave before the requested time, apply the
   walking limit, and return at most ``search_itineraries`` routes.

Errors are never cached. Redis being down only removes caching and
cross-instance coalescing; searches still work.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import structlog
from pydantic import BaseModel

from app.cache.redis import ResilientRedis
from app.core.config import Settings
from app.core.freshness import SourceKind, classify
from app.core.graph_version import GraphVersion
from app.core.single_flight import SingleFlight
from app.integrations.otp.client import OtpClient, PlanQuery
from app.integrations.otp.mapper import to_route
from app.schemas.routes import (
    DataFreshness,
    Notice,
    Route,
    RouteSearchRequest,
    RouteSearchResponse,
)
from app.services.accessibility.assessor import assess_route
from app.services.accessibility.snapshot import ELEVATOR_SOURCE, SnapshotProvider
from app.services.cache_keys import departure_bucket, route_search_key

logger = structlog.get_logger(__name__)

NEAR_TERM = timedelta(hours=1)
LOCK_POLL_INTERVAL_S = 0.1
# Extra itineraries requested so that filtering out departures earlier than
# the requested time (the bucket starts up to 5 minutes before it) still
# leaves enough routes.
EXTRA_ITINERARIES = 3

# OTP routing error codes mapped to messages safe to show users.
ROUTING_NOTICES = {
    "NO_TRANSIT_CONNECTION": "No transit connection was found for this trip and time.",
    "NO_TRANSIT_CONNECTION_IN_SEARCH_WINDOW": "No transit service runs in the time window.",
    "OUTSIDE_SERVICE_PERIOD": "The selected time is outside the published timetables.",
    "OUTSIDE_BOUNDS": "One of the locations is outside the supported area.",
    "LOCATION_NOT_FOUND": "One of the locations could not be reached from the street network.",
    "NO_STOPS_IN_RANGE": "No transit stops were found within walking distance.",
    "WALKING_BETTER_THAN_TRANSIT": "Walking is faster than any transit option for this trip.",
}


class CacheStatus(StrEnum):
    HIT = "hit"
    MISS = "miss"
    SHARED = "shared"


class CachedSearch(BaseModel):
    routes: list[Route]
    notices: list[Notice]


@dataclass(frozen=True)
class SearchOutcome:
    response: RouteSearchResponse
    cache: CacheStatus
    # Identify the search for background work that depends on it.
    cache_key: str
    requested: datetime


def plan_query(request: RouteSearchRequest, earliest: datetime, itineraries: int) -> PlanQuery:
    """The OTP query for a search; suggestion checks reuse it so that a
    suggestion is planned under exactly the rider's settings."""
    accessibility, optimization = request.accessibility, request.optimization
    less_walking = optimization.primary == "LEAST_WALKING" or optimization.prefer_less_walking
    fewer_transfers = (
        optimization.primary == "FEWEST_TRANSFERS" or optimization.prefer_fewer_transfers
    )
    return PlanQuery(
        origin=(request.origin.lat, request.origin.lon),
        destination=(request.destination.lat, request.destination.lon),
        earliest_departure=earliest,
        itineraries=itineraries,
        # OTP has a single accessibility mode. NYC feeds publish no
        # wheelchair_boarding data, so OTP can only penalise unknown stops,
        # not exclude them; verdicts come from the accessibility assessor.
        wheelchair=accessibility.needs_accessible_routing,
        walk_reluctance=4.0 if less_walking else None,
        transfer_cost=600 if fewer_transfers else None,
    )


class RouteSearchService:
    def __init__(
        self,
        otp: OtpClient,
        redis: ResilientRedis,
        graph_version: GraphVersion,
        accessibility: SnapshotProvider,
        settings: Settings,
    ) -> None:
        self._otp = otp
        self._redis = redis
        self._graph_version = graph_version
        self._accessibility = accessibility
        self._settings = settings
        self._secret = settings.key_hashing_secret.get_secret_value().encode()
        self._single_flight: SingleFlight[tuple[CachedSearch, CacheStatus]] = SingleFlight()

    async def search(self, request: RouteSearchRequest) -> SearchOutcome:
        started = time.perf_counter()
        now = datetime.now(UTC)
        requested = request.depart_at or now
        bucket = departure_bucket(requested)
        graph_version = self._graph_version.current()
        key = route_search_key(request, bucket, graph_version, self._secret)

        result, status = await self._cached_or_computed(key, request, bucket, now)
        routes, notices = self._finalize(result, request, requested)

        # Assessed on every response, cached or not: a cached itinerary never
        # carries forward an accessibility verdict from when it was computed.
        snapshot = await self._accessibility.current()
        wheelchair = request.accessibility.wheelchair_required
        routes = [assess_route(route, snapshot, wheelchair, now) for route in routes]
        review_only: list[Route] = []
        if request.accessibility.needs_accessible_routing:
            routes, review_only = (
                [r for r in routes if r.accessibility_status == "meets"],
                [r for r in routes if r.accessibility_status != "meets"],
            )
            if not routes and review_only:
                notices.append(
                    Notice(
                        code="NO_CONFIRMED_ACCESSIBLE_ROUTE",
                        message=(
                            "No route could be confirmed to meet your access needs. "
                            "Alternatives are listed for review with each known barrier "
                            "and unconfirmed step."
                        ),
                    )
                )

        logger.info(
            "route_search",
            cache=status,
            routes=len(routes),
            review_only=len(review_only),
            accessibility=[r.accessibility_status for r in routes + review_only],
            notices=[notice.code for notice in notices],
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        response = RouteSearchResponse(
            search_id=uuid.uuid4().hex,
            graph_version=graph_version,
            routes=routes,
            review_only_routes=review_only,
            notices=notices,
            data_freshness=[
                DataFreshness(
                    source=ELEVATOR_SOURCE,
                    freshness=classify(
                        SourceKind.ACCESSIBILITY_STATUS, snapshot.observed_at, None, now
                    ).value,
                    as_of=snapshot.observed_at,
                )
            ],
        )
        return SearchOutcome(response=response, cache=status, cache_key=key, requested=requested)

    async def _cached_or_computed(
        self, key: str, request: RouteSearchRequest, bucket: datetime, now: datetime
    ) -> tuple[CachedSearch, CacheStatus]:
        if (cached := await self._read_cache(key)) is not None:
            return cached, CacheStatus.HIT

        (result, status), shared = await self._single_flight.run(
            key, lambda: self._compute_once_across_instances(key, request, bucket, now)
        )
        return result, CacheStatus.SHARED if shared else status

    async def _compute_once_across_instances(
        self, key: str, request: RouteSearchRequest, bucket: datetime, now: datetime
    ) -> tuple[CachedSearch, CacheStatus]:
        lock_key = f"{key}:lock"
        token = uuid.uuid4().hex
        acquired = await self._redis.set_if_absent(
            lock_key, token, self._settings.single_flight_lock_ttl_ms
        )
        if acquired is False:
            # Another instance is computing this search; wait briefly for its result.
            deadline = time.monotonic() + self._settings.single_flight_wait_s
            while time.monotonic() < deadline:
                await asyncio.sleep(LOCK_POLL_INTERVAL_S)
                if (cached := await self._read_cache(key)) is not None:
                    return cached, CacheStatus.SHARED
            logger.info("single_flight_wait_expired")

        try:
            result = await self._compute(request, bucket)
            await self._write_cache(key, result, bucket, now)
            return result, CacheStatus.MISS
        finally:
            if acquired:
                await self._redis.delete_if_equals(lock_key, token)

    async def _compute(self, request: RouteSearchRequest, bucket: datetime) -> CachedSearch:
        query = plan_query(request, bucket, self._settings.search_itineraries + EXTRA_ITINERARIES)
        plan = await self._otp.plan(query)
        # OTP reports some errors once per endpoint (origin and destination);
        # show each distinct problem once.
        codes = dict.fromkeys(err["code"] for err in plan.routing_errors)
        descriptions = {err["code"]: err["description"] for err in plan.routing_errors}
        notices = [
            Notice(code=code, message=ROUTING_NOTICES.get(code, descriptions[code]))
            for code in codes
        ]
        return CachedSearch(routes=[to_route(it) for it in plan.itineraries], notices=notices)

    def _finalize(
        self, result: CachedSearch, request: RouteSearchRequest, requested: datetime
    ) -> tuple[list[Route], list[Notice]]:
        # Leave a minute of slack so "depart now" still shows a train leaving now.
        earliest = requested - timedelta(minutes=1)
        routes = [route for route in result.routes if route.departure >= earliest]
        notices = list(result.notices)

        max_walk = request.accessibility.max_walk_meters
        if max_walk is not None:
            within = [route for route in routes if route.walk_meters <= max_walk]
            if routes and not within:
                notices.append(
                    Notice(
                        code="WALK_LIMIT_EXCEEDED",
                        message=f"Every route found needs more than {max_walk} m of walking.",
                    )
                )
            routes = within
        return routes[: self._settings.search_itineraries], notices

    async def _read_cache(self, key: str) -> CachedSearch | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            return CachedSearch.model_validate_json(raw)
        except ValueError:
            logger.warning("route_cache_entry_invalid")
            return None

    async def _write_cache(
        self, key: str, result: CachedSearch, bucket: datetime, now: datetime
    ) -> None:
        if not result.routes:
            ttl = self._settings.route_cache_empty_ttl_s
        elif bucket - now < NEAR_TERM:
            # Near-term departures are the ones realtime updates will change.
            ttl = self._settings.route_cache_near_term_ttl_s
        else:
            ttl = self._settings.route_cache_ttl_s
        await self._redis.set(key, result.model_dump_json().encode(), ttl_s=ttl)
