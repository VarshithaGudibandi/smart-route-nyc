"""HTTP client for the OpenTripPlanner GTFS GraphQL API.

Each call has a timeout, transient failures (connection errors, timeouts,
502/503/504) are retried with full-jitter backoff, and a circuit breaker
stops calls to an OTP instance that keeps failing. GraphQL validation errors
are never retried: repeating a malformed query cannot succeed.
"""

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import structlog

from app.core.errors import RoutingTimeoutError, RoutingUnavailableError
from app.core.resilience import CircuitBreaker, CircuitOpenError

logger = structlog.get_logger(__name__)

RETRYABLE_STATUS = {502, 503, 504}
BACKOFF_BASE_S = 0.2
BACKOFF_CAP_S = 1.0

PLAN_QUERY = """
query Plan(
  $origin: PlanLabeledLocationInput!
  $destination: PlanLabeledLocationInput!
  $dateTime: PlanDateTimeInput!
  $first: Int!
  $preferences: PlanPreferencesInput
  $via: [PlanViaLocationInput!]
) {
  planConnection(
    origin: $origin
    destination: $destination
    dateTime: $dateTime
    first: $first
    preferences: $preferences
    via: $via
  ) {
    edges {
      node {
        start
        end
        duration
        numberOfTransfers
        walkDistance
        walkTime
        legs {
          mode
          distance
          headsign
          route { gtfsId shortName longName }
          agency { name }
          trip { gtfsId }
          from { name lat lon stop { gtfsId parentStation { gtfsId } } }
          to { name lat lon stop { gtfsId parentStation { gtfsId } } }
          start { scheduledTime estimated { time } }
          end { scheduledTime estimated { time } }
          legGeometry { points }
          steps {
            lat
            lon
            feature {
              __typename
              ... on Entrance { name wheelchairAccessible }
            }
          }
        }
      }
    }
    routingErrors { code description }
  }
}
"""

# Scheduled departures from every platform of a station, grouped by stopping
# pattern, with each pattern's full stop sequence. Used to tell which lines
# run between two stations and how often, before asking OTP to verify a
# specific transfer.
_DEPARTURES_FIELDS = """
    gtfsId
    name
    stoptimesForPatterns(
      startTime: $start
      timeRange: $range
      numberOfDepartures: $perPattern
      omitNonPickups: true
      omitCanceled: true
    ) {
      pattern {
        code
        directionId
        headsign
        route { gtfsId shortName mode }
        stops { gtfsId name parentStation { gtfsId name } }
      }
      stoptimes { scheduledDeparture serviceDay }
    }
"""
# PATH platforms have no parent station in GTFS, so the API's station ID for
# them is the platform's own stop ID: ask for both and use whichever exists.
STATION_DEPARTURES_QUERY = f"""
query StationDepartures($id: String!, $start: Long!, $range: Int!, $perPattern: Int!) {{
  station(id: $id) {{ {_DEPARTURES_FIELDS} }}
  stop(id: $id) {{ {_DEPARTURES_FIELDS} }}
}}
"""


@dataclass(frozen=True)
class PlanQuery:
    origin: tuple[float, float]
    destination: tuple[float, float]
    earliest_departure: datetime
    itineraries: int
    wheelchair: bool = False
    walk_reluctance: float | None = None
    transfer_cost: int | None = None
    # Verification of a proposed transfer: OTP may only use these routes (GTFS
    # IDs, for the whole trip) and must stop at each via station.
    include_routes: tuple[str, ...] = ()
    via_stations: tuple[str, ...] = ()

    def variables(self) -> dict[str, Any]:
        preferences: dict[str, Any] = {}
        if self.wheelchair:
            preferences["accessibility"] = {"wheelchair": {"enabled": True}}
        if self.walk_reluctance is not None:
            preferences["street"] = {"walk": {"reluctance": self.walk_reluctance}}
        if self.transfer_cost is not None:
            preferences.setdefault("transit", {})["transfer"] = {"cost": self.transfer_cost}
        if self.include_routes:
            preferences.setdefault("transit", {})["filters"] = [
                {"include": [{"routes": list(self.include_routes)}]}
            ]
        return {
            "origin": {"location": _coordinate(*self.origin)},
            "destination": {"location": _coordinate(*self.destination)},
            "dateTime": {"earliestDeparture": self.earliest_departure.isoformat()},
            "first": self.itineraries,
            "preferences": preferences or None,
            "via": [{"visit": {"stopLocationIds": [station]}} for station in self.via_stations]
            or None,
        }


@dataclass(frozen=True)
class PlanResult:
    itineraries: list[dict[str, Any]]
    routing_errors: list[dict[str, str]]


class OtpQueryError(Exception):
    """OTP rejected the query itself; this is a bug on our side, not an outage."""


class OtpClient:
    GRAPHQL_PATH = "/otp/gtfs/v1"

    def __init__(self, http: httpx.AsyncClient, breaker: CircuitBreaker, retries: int = 1) -> None:
        self._http = http
        self.breaker = breaker
        self.retries = retries

    @classmethod
    def create(
        cls,
        base_url: str,
        timeout_s: float,
        retries: int,
        failure_threshold: int,
        reset_timeout_s: float,
    ) -> "OtpClient":
        http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout_s))
        return cls(http, CircuitBreaker("otp", failure_threshold, reset_timeout_s), retries)

    async def plan(self, query: PlanQuery) -> PlanResult:
        data = await self._execute(PLAN_QUERY, query.variables())
        connection = data["planConnection"]
        return PlanResult(
            itineraries=[edge["node"] for edge in connection["edges"]],
            routing_errors=connection["routingErrors"],
        )

    async def station_departures(
        self, station_id: str, start: datetime, window_s: int, per_pattern: int
    ) -> dict[str, Any] | None:
        """Departures by pattern; ``None`` if OTP does not know the station."""
        data = await self._execute(
            STATION_DEPARTURES_QUERY,
            {
                "id": station_id,
                "start": int(start.timestamp()),
                "range": window_s,
                "perPattern": per_pattern,
            },
        )
        station: dict[str, Any] | None = data["station"] or data["stop"]
        return station

    async def is_ready(self, timeout_s: float) -> bool:
        """True if OTP answers a trivial query. Bypasses the breaker on purpose."""
        try:
            response = await self._http.post(
                self.GRAPHQL_PATH, json={"query": "{ __typename }"}, timeout=timeout_s
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200 and "errors" not in response.json()

    async def close(self) -> None:
        await self._http.aclose()

    async def _execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                self.breaker.before_call()
            except CircuitOpenError as exc:
                raise RoutingUnavailableError(
                    "The routing engine is temporarily unavailable"
                ) from exc
            try:
                response = await self._http.post(
                    self.GRAPHQL_PATH, json={"query": query, "variables": variables}
                )
                if response.status_code in RETRYABLE_STATUS:
                    raise _TransientError(f"HTTP {response.status_code}")
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
            except (httpx.TimeoutException, httpx.TransportError, _TransientError) as exc:
                self.breaker.record_failure()
                logger.warning(
                    "otp_request_failed",
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    breaker_state=self.breaker.state,
                )
                if attempt == attempts:
                    if isinstance(exc, httpx.TimeoutException):
                        raise RoutingTimeoutError(
                            "The routing engine did not respond in time"
                        ) from exc
                    raise RoutingUnavailableError(
                        "The routing engine is temporarily unavailable"
                    ) from exc
                await asyncio.sleep(
                    random.uniform(0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt))
                )
                continue
            except httpx.HTTPStatusError as exc:
                self.breaker.record_success()
                raise OtpQueryError(f"OTP answered HTTP {exc.response.status_code}") from exc

            self.breaker.record_success()
            if payload.get("errors"):
                raise OtpQueryError(f"GraphQL errors: {payload['errors']}")
            data: dict[str, Any] = payload["data"]
            return data
        raise AssertionError("unreachable")


class _TransientError(Exception):
    pass


def _coordinate(lat: float, lon: float) -> dict[str, Any]:
    return {"coordinate": {"latitude": lat, "longitude": lon}}
