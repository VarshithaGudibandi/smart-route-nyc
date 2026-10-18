"""Measure how often transfer suggestions beat OpenTripPlanner's own routes.

Runs against a live stack (OTP, PostgreSQL with ingested elevator and
alert data, Redis), so it is not part of CI:

    uv run python -m evals.suggestions
    uv run python -m evals.suggestions --json > suggestion-report.json

Each trip in ``suggestion_trips.jsonl`` (public landmark locations, rounded)
is searched at four times of the coming week, without and with a step-free
requirement. For each search the investigation records whether it applied,
how many proposals were made, how many OTP verified, the time saved and
why proposals were rejected.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as clock
from pathlib import Path
from typing import Any

from app.cache.redis import ResilientRedis
from app.core.config import get_settings
from app.core.graph_version import GraphVersion
from app.core.logging import configure_logging
from app.db.session import create_engine
from app.integrations.otp.client import OtpClient
from app.schemas.routes import (
    AccessibilityRequirements,
    Location,
    OptimizationPreferences,
    RouteSearchRequest,
)
from app.services.accessibility.snapshot import SnapshotProvider
from app.services.cache_keys import NEW_YORK
from app.services.route_search import RouteSearchService
from app.services.suggestions.service import SuggestionService
from app.services.transit.alerts import AlertsProvider
from app.services.transit.knowledge import TransitKnowledge

TRIPS_FILE = Path(__file__).with_name("suggestion_trips.jsonl")


@dataclass
class Run:
    trip: str
    when: str
    step_free: bool
    baseline_routes: int
    applicable: bool
    proposals: int
    verified: int
    best_saving_s: int | None
    rejections: list[str]
    duration_ms: float


@dataclass
class Report:
    graph_version: str
    searches: int
    with_baseline: int
    applicable_rate: float
    proposals_per_applicable: float
    searches_improved: int
    improved_rate: float
    savings_s: list[int]
    rejection_codes: dict[str, int]
    not_applicable_reasons: dict[str, int]
    latency_p50_ms: float
    latency_p95_ms: float
    runs: list[Run] = field(default_factory=list)


def departure_times(today: date) -> dict[str, datetime]:
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    saturday = monday + timedelta(days=5)
    at = {
        "weekday_08:30": (monday, clock(8, 30)),
        "weekday_13:00": (monday, clock(13, 0)),
        "weekday_23:00": (monday, clock(23, 0)),
        "saturday_11:00": (saturday, clock(11, 0)),
    }
    return {name: datetime.combine(d, t, NEW_YORK) for name, (d, t) in at.items()}


def request(trip: dict[str, Any], depart_at: datetime, step_free: bool) -> RouteSearchRequest:
    # model_construct: departure times are fixed per run and may already be
    # past by the time a long run finishes; validation would reject them.
    return RouteSearchRequest.model_construct(
        origin=Location(**trip["origin"]),
        destination=Location(**trip["destination"]),
        depart_at=depart_at,
        accessibility=AccessibilityRequirements(step_free_required=step_free),
        optimization=OptimizationPreferences(),
    )


async def evaluate(trips: list[dict[str, Any]]) -> Report:
    settings = get_settings()
    redis = ResilientRedis.from_url(
        str(settings.redis_url), timeout_ms=500, failure_threshold=5, reset_timeout_s=10
    )
    engine = create_engine(settings)
    otp = OtpClient.create(settings.otp_base_url, 60, 0, 10, 10)
    graph = GraphVersion(settings.otp_graph_version_file)
    accessibility = SnapshotProvider(engine, redis)
    search = RouteSearchService(otp, redis, graph, accessibility, settings)
    suggestions = SuggestionService(
        otp, TransitKnowledge(otp, redis, graph), AlertsProvider(engine, redis), accessibility
    )

    runs: list[Run] = []
    reasons: Counter[str] = Counter()
    try:
        for when, depart_at in departure_times(date.today()).items():
            for trip in trips:
                for step_free in (False, True):
                    req = request(trip, depart_at, step_free)
                    started = time.perf_counter()
                    outcome = await search.search(req)
                    baseline = outcome.response.routes or outcome.response.review_only_routes
                    result = await suggestions.investigate(req, baseline)
                    if not result.applicable and result.reason:
                        reasons[result.reason] += 1
                    runs.append(
                        Run(
                            trip=trip["id"],
                            when=when,
                            step_free=step_free,
                            baseline_routes=len(baseline),
                            applicable=result.applicable,
                            proposals=len(result.suggestions) + len(result.rejected),
                            verified=len(result.suggestions),
                            best_saving_s=max(
                                (s.saves_seconds for s in result.suggestions), default=None
                            ),
                            rejections=[r.code for r in result.rejected],
                            duration_ms=round((time.perf_counter() - started) * 1000, 1),
                        )
                    )
    finally:
        await otp.close()
        await engine.dispose()
        await redis.close()
    return summarize(graph.current(), runs, reasons)


def summarize(graph_version: str, runs: list[Run], reasons: Counter[str]) -> Report:
    with_baseline = [r for r in runs if r.baseline_routes]
    applicable = [r for r in with_baseline if r.applicable]
    improved = [r for r in applicable if r.verified]
    latencies = sorted(r.duration_ms for r in runs)

    def rate(n: int, d: int) -> float:
        return round(n / d, 3) if d else 0.0

    return Report(
        graph_version=graph_version,
        searches=len(runs),
        with_baseline=len(with_baseline),
        applicable_rate=rate(len(applicable), len(with_baseline)),
        proposals_per_applicable=round(statistics.mean(r.proposals for r in applicable), 2)
        if applicable
        else 0.0,
        searches_improved=len(improved),
        improved_rate=rate(len(improved), len(applicable)),
        savings_s=sorted(r.best_saving_s for r in improved if r.best_saving_s is not None),
        rejection_codes=dict(Counter(c for r in runs for c in r.rejections).most_common()),
        not_applicable_reasons=dict(reasons.most_common()),
        latency_p50_ms=statistics.median(latencies) if latencies else 0.0,
        latency_p95_ms=latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0.0,
        runs=runs,
    )


def print_report(report: Report) -> None:
    print(f"Transfer suggestions vs OTP baseline (graph {report.graph_version})\n")
    print(f"  Searches                         {report.searches}")
    print(f"  With a baseline route            {report.with_baseline}")
    print(f"  Investigation applicable         {report.applicable_rate:.1%}")
    print(f"  Proposals per applicable search  {report.proposals_per_applicable}")
    print(
        f"  Searches with a verified faster  {report.searches_improved} "
        f"({report.improved_rate:.1%} of applicable)"
    )
    if report.savings_s:
        print(
            f"  Time saved                       median {statistics.median(report.savings_s)} s,"
            f" max {max(report.savings_s)} s"
        )
    print(
        f"  Latency p50 / p95 (search + investigation)  "
        f"{report.latency_p50_ms} / {report.latency_p95_ms} ms"
    )
    print("\n  Rejections:", ", ".join(f"{k} {v}" for k, v in report.rejection_codes.items()))
    print("  Not applicable:")
    for reason, count in report.not_applicable_reasons.items():
        print(f"    {count:4}  {reason}")
    improved = [r for r in report.runs if r.verified]
    if improved:
        print("\n  Improved searches:")
        for r in improved:
            print(f"    {r.trip} {r.when} step_free={r.step_free}: saves {r.best_saving_s} s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--limit", type=int, help="only the first N trips")
    args = parser.parse_args()
    configure_logging("WARNING")
    trips = [json.loads(line) for line in TRIPS_FILE.read_text().splitlines() if line.strip()]
    report = asyncio.run(evaluate(trips[: args.limit] if args.limit else trips))
    if args.json:
        print(json.dumps(asdict(report), indent=2, default=str))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
