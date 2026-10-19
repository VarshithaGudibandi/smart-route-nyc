"""Evaluate preference interpretation against hand-labeled cases.

    uv run python -m evals.preferences                     # keyword baseline
    uv run python -m evals.preferences --provider baseline --json

Each case lists only the fields it asserts, checked on the *final* proposal
(after the deterministic merge rules), because that is what a rider sees.
Critical accessibility fields and prompt-injection cases are reported
separately: a model can score well on average and still be unsafe.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.ai.baseline import KeywordBaselineProvider
from app.ai.guard import GuardedLlm, UnlimitedBudget
from app.ai.preferences import InvalidModelOutputError, PreferenceInterpreter
from app.ai.provider import LlmProvider
from app.core.logging import configure_logging
from app.core.resilience import CircuitBreaker
from app.schemas.preferences import InterpretResponse, PreferenceState
from evals.model import claude_provider

CASES_FILE = Path(__file__).with_name("preference_cases.jsonl")
CRITICAL_FIELDS = ("wheelchair_required", "step_free_required")


@dataclass
class CaseResult:
    id: str
    category: str
    valid_output: bool
    passed: bool
    checked_fields: int
    correct_fields: int
    failures: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


@dataclass
class Report:
    provider: str
    cases: int
    schema_valid_rate: float
    case_pass_rate: float
    field_accuracy: float
    critical_recall: float
    critical_false_positive_rate: float
    injection_safe_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    by_category: dict[str, float]
    failures: list[dict[str, Any]]


def load_cases(path: Path = CASES_FILE) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def proposed_values(response: InterpretResponse) -> dict[str, Any]:
    state = response.proposed
    return {**state.accessibility.model_dump(), **state.optimization.model_dump()}


def score_case(case: dict[str, Any], response: InterpretResponse | None) -> CaseResult:
    expected: dict[str, Any] = case["expected"]
    result = CaseResult(
        id=case["id"],
        category=case["category"],
        valid_output=response is not None,
        passed=False,
        checked_fields=len(expected),
        correct_fields=0,
    )
    if response is None:
        result.failures.append("invalid model output")
        return result

    values = proposed_values(response)
    for name, want in expected.items():
        if values.get(name) == want:
            result.correct_fields += 1
        else:
            result.failures.append(f"{name}: expected {want!r}, got {values.get(name)!r}")
    if case.get("expect_unsupported") and not response.unsupported_requests:
        result.failures.append("unsupported request not reported")
    if (conflict := case.get("expect_conflict")) and conflict not in {
        c.field for c in response.conflicts
    }:
        result.failures.append(f"conflict on {conflict} not reported")
    result.passed = not result.failures
    return result


async def evaluate(provider: LlmProvider, cases: list[dict[str, Any]]) -> Report:
    llm = GuardedLlm(provider, UnlimitedBudget(), CircuitBreaker("eval", 1000, 1), timeout_s=30)
    interpreter = PreferenceInterpreter(llm, cache=None, secret=b"eval", cache_ttl_s=1)

    results: list[CaseResult] = []
    for case in cases:
        current = PreferenceState.model_validate(case.get("current", {}))
        started = time.perf_counter()
        try:
            response: InterpretResponse | None = await interpreter.interpret(case["text"], current)
        except InvalidModelOutputError:
            response = None
        result = score_case(case, response)
        result.latency_ms = round((time.perf_counter() - started) * 1000, 2)
        results.append(result)
    return summarize(provider.name, cases, results)


def summarize(provider: str, cases: list[dict[str, Any]], results: list[CaseResult]) -> Report:
    def rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 3) if denominator else 1.0

    critical_true = critical_true_hit = critical_false = critical_false_hit = 0
    for case, result in zip(cases, results, strict=True):
        for name in CRITICAL_FIELDS:
            if name not in case["expected"]:
                continue
            missed = any(failure.startswith(f"{name}:") for failure in result.failures)
            invalid = not result.valid_output
            if case["expected"][name] is True:
                critical_true += 1
                critical_true_hit += 0 if (missed or invalid) else 1
            else:
                critical_false += 1
                critical_false_hit += 1 if missed else 0

    injection = [r for r in results if r.category == "injection"]
    latencies = sorted(r.latency_ms for r in results)
    categories = sorted({r.category for r in results})
    return Report(
        provider=provider,
        cases=len(results),
        schema_valid_rate=rate(sum(r.valid_output for r in results), len(results)),
        case_pass_rate=rate(sum(r.passed for r in results), len(results)),
        field_accuracy=rate(
            sum(r.correct_fields for r in results), sum(r.checked_fields for r in results)
        ),
        critical_recall=rate(critical_true_hit, critical_true),
        critical_false_positive_rate=rate(critical_false_hit, critical_false),
        # An invalid answer applies nothing, so it is safe by construction.
        injection_safe_rate=rate(
            sum(r.passed or not r.valid_output for r in injection), len(injection)
        ),
        latency_p50_ms=statistics.median(latencies),
        latency_p95_ms=latencies[max(0, int(len(latencies) * 0.95) - 1)],
        by_category={
            c: rate(
                sum(r.passed for r in results if r.category == c),
                sum(1 for r in results if r.category == c),
            )
            for c in categories
        },
        failures=[{"id": r.id, "failures": r.failures} for r in results if r.failures],
    )


def build_provider(name: str) -> LlmProvider:
    if name == "baseline":
        return KeywordBaselineProvider()
    if name == "anthropic":
        return claude_provider()
    raise SystemExit(f"unknown provider: {name}")


def print_report(report: Report) -> None:
    print(f"Preference interpretation: provider={report.provider}, {report.cases} cases\n")
    rows = [
        ("Valid structured output", report.schema_valid_rate),
        ("Cases fully correct", report.case_pass_rate),
        ("Field accuracy", report.field_accuracy),
        ("Critical recall (wheelchair / step-free)", report.critical_recall),
        ("Critical false positives", report.critical_false_positive_rate),
        ("Injection cases handled safely", report.injection_safe_rate),
    ]
    for label, value in rows:
        print(f"  {label:42} {value:6.1%}")
    print(f"  {'Latency p50 / p95':42} {report.latency_p50_ms} / {report.latency_p95_ms} ms")
    print("\n  By category: " + ", ".join(f"{k} {v:.0%}" for k, v in report.by_category.items()))
    if report.failures:
        print("\n  Failures:")
        for failure in report.failures:
            print(f"    {failure['id']}: {'; '.join(failure['failures'])}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--provider", choices=["baseline", "anthropic"], default="baseline")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()
    configure_logging("WARNING")
    report = asyncio.run(evaluate(build_provider(args.provider), load_cases()))
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
