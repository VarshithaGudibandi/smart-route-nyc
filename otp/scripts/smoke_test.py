"""Smoke tests against a running OpenTripPlanner instance.

Checks:
  subway  Plans Chambers St -> 116 St-Columbia and verifies that an express-to-local
          transfer can be enforced: a valid transfer must verify, an impossible one
          (the express does not stop at 110 St) must be rejected.
  path    Plans Journal Square (PATH) -> Columbia University by coordinates and
          requires an itinerary that uses both PATH and the subway.

Usage:
    python otp/scripts/smoke_test.py [--url http://localhost:8080] [--check subway|path|all]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

PLAN_QUERY = """
query Plan($origin: PlanLabeledLocationInput!, $destination: PlanLabeledLocationInput!,
           $dateTime: PlanDateTimeInput!, $preferences: PlanPreferencesInput,
           $via: [PlanViaLocationInput!], $first: Int) {
  planConnection(origin: $origin, destination: $destination, dateTime: $dateTime,
                 preferences: $preferences, via: $via, first: $first) {
    edges { node {
      start end numberOfTransfers
      legs {
        mode
        route { gtfsId shortName }
        from { name stop { gtfsId parentStation { gtfsId } } }
        to { name stop { gtfsId parentStation { gtfsId } } }
        start { scheduledTime }
        end { scheduledTime }
      }
    } }
    routingErrors { code description }
  }
}
"""

Itinerary = dict[str, Any]


class OtpClient:
    def __init__(self, base_url: str) -> None:
        self.endpoint = base_url.rstrip("/") + "/otp/gtfs/v1"

    def plan(
        self,
        origin: dict[str, Any],
        destination: dict[str, Any],
        departure: datetime,
        routes: list[str] | None = None,
        via: list[dict[str, Any]] | None = None,
        first: int = 3,
    ) -> list[Itinerary]:
        preferences = (
            {"transit": {"filters": [{"include": [{"routes": routes}]}]}} if routes else None
        )
        variables = {
            "origin": {"location": origin},
            "destination": {"location": destination},
            "dateTime": {"earliestDeparture": departure.isoformat()},
            "preferences": preferences,
            "via": via,
            "first": first,
        }
        body = json.dumps({"query": PLAN_QUERY, "variables": variables}).encode()
        request = urllib.request.Request(self.endpoint, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if "errors" in payload:
            raise RuntimeError(f"GraphQL errors: {payload['errors']}")
        connection = payload["data"]["planConnection"]
        if connection["routingErrors"]:
            print(f"  routing errors: {connection['routingErrors']}")
        return [edge["node"] for edge in connection["edges"]]


def stop(stop_id: str) -> dict[str, Any]:
    return {"stopLocation": {"stopLocationId": stop_id}}


def coordinate(lat: float, lon: float) -> dict[str, Any]:
    return {"coordinate": {"latitude": lat, "longitude": lon}}


def station_of(place: dict[str, Any]) -> str | None:
    """Return the parent-station id of a leg endpoint.

    MTA subway itineraries board at platform stops (e.g. ``120S``); proposals
    reference stations (``120``), so comparisons happen at the station level.
    """
    stop_ = place.get("stop")
    if not stop_:
        return None
    parent = stop_.get("parentStation")
    return parent["gtfsId"] if parent else stop_["gtfsId"]


def next_weekday_morning() -> datetime:
    now = datetime.now(NEW_YORK)
    day = now + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.replace(hour=8, minute=30, second=0, microsecond=0)


def describe(itinerary: Itinerary) -> str:
    parts = []
    for leg in itinerary["legs"]:
        name = f"({leg['route']['shortName']})" if leg["route"] else ""
        parts.append(
            f"{leg['mode']}{name} {leg['from']['name']} -> {leg['to']['name']} "
            f"{leg['start']['scheduledTime'][11:16]}-{leg['end']['scheduledTime'][11:16]}"
        )
    return f"{itinerary['start'][11:16]} -> {itinerary['end'][11:16]}: " + " | ".join(parts)


def verify_transfer(
    client: OtpClient,
    origin: str,
    destination: str,
    transfer_station: str,
    first_routes: list[str],
    second_routes: list[str],
    departure: datetime,
) -> Itinerary | None:
    """Return an itinerary that follows the proposed transfer exactly, or None.

    OTP filters routes for the whole trip, not per leg, so the query restricts
    routes to the union and forces a visit at the transfer station; each
    returned itinerary is then checked leg by leg. Timing comes from OTP,
    which applies the feed's minimum transfer times.
    """
    candidates = client.plan(
        stop(origin),
        stop(destination),
        departure,
        routes=first_routes + second_routes,
        via=[{"visit": {"stopLocationIds": [transfer_station]}}],
        first=5,
    )
    for itinerary in candidates:
        transit = [leg for leg in itinerary["legs"] if leg["route"]]
        if len(transit) != 2:
            continue
        first, second = transit
        if (
            first["route"]["gtfsId"] in first_routes
            and station_of(first["from"]) == origin
            and station_of(first["to"]) == transfer_station
            and second["route"]["gtfsId"] in second_routes
            and station_of(second["from"]) == transfer_station
            and station_of(second["to"]) == destination
        ):
            return itinerary
    return None


def check_subway(client: OtpClient, departure: datetime) -> bool:
    chambers, columbia = "MTASBWY:137", "MTASBWY:117"
    express, local = ["MTASBWY:2", "MTASBWY:3"], ["MTASBWY:1"]

    baseline = client.plan(stop(chambers), stop(columbia), departure)
    print(f"Baseline Chambers St -> 116 St: {len(baseline)} itineraries")
    for itinerary in baseline:
        print("  " + describe(itinerary))

    valid = verify_transfer(client, chambers, columbia, "MTASBWY:120", express, local, departure)
    invalid = verify_transfer(client, chambers, columbia, "MTASBWY:118", express, local, departure)
    print("Express to 96 St, then local:", "verified" if valid else "NOT verified")
    if valid:
        print("  " + describe(valid))
    print(
        "Express to 110 St, then local (impossible):", "rejected" if invalid is None else "ACCEPTED"
    )
    return bool(baseline) and valid is not None and invalid is None


def check_path(client: OtpClient, departure: datetime) -> bool:
    journal_square = coordinate(40.73301, -74.06289)
    columbia = coordinate(40.80755, -73.96449)
    itineraries = client.plan(journal_square, columbia, departure, first=5)
    print(f"Journal Square -> Columbia University: {len(itineraries)} itineraries")
    crosses_hudson = False
    for itinerary in itineraries:
        print("  " + describe(itinerary))
        feeds = {leg["route"]["gtfsId"].split(":")[0] for leg in itinerary["legs"] if leg["route"]}
        crosses_hudson |= {"PATH", "MTASBWY"} <= feeds
    print("Uses PATH and subway:", "yes" if crosses_hudson else "NO")
    return crosses_hudson


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--check", choices=["subway", "path", "all"], default="all")
    args = parser.parse_args()

    client = OtpClient(args.url)
    departure = next_weekday_morning()
    print(f"Departure: {departure.isoformat()}\n")

    checks = {"subway": check_subway, "path": check_path}
    selected = checks if args.check == "all" else {args.check: checks[args.check]}
    results = {}
    for name, check in selected.items():
        started = time.perf_counter()
        results[name] = check(client, departure)
        elapsed = time.perf_counter() - started
        print(f"[{'PASS' if results[name] else 'FAIL'}] {name} ({elapsed:.2f}s)\n")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
