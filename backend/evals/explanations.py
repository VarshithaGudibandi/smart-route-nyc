"""Check that the explanation grounding rules accept faithful wording and
reject wording that is not supported by the route's facts.

    uv run python -m evals.explanations

Each case in ``explanation_cases.jsonl`` is a model reply written against
one route (``explanation_scenario.py``). Unfaithful and malformed replies
must be rejected, for the expected reason; faithful ones must be accepted.
The command exits non-zero otherwise, so CI blocks a weaker check.

"known_gap" cases are wording the lexical checks cannot catch (reversed
negation, advice, a number reused with a new meaning). They are reported so
the limits stay visible, and do not fail the run.

When a model provider is configured, the same facts can be sent to it and
its replies scored with ``check_grounding``.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from app.ai.explanations import InvalidExplanationError, check_grounding, parse_explanation
from evals.explanation_scenario import scenario_facts

CASES_FILE = Path(__file__).with_name("explanation_cases.jsonl")


@dataclass
class Outcome:
    id: str
    category: str
    accepted: bool
    problems: list[str]
    ok: bool


def run_case(case: dict[str, str | None]) -> Outcome:
    facts = scenario_facts()
    try:
        explanation = parse_explanation(str(case["reply"]))
        problems = [p.message for p in check_grounding(explanation, facts)]
    except InvalidExplanationError as exc:
        problems = ["schema: " + p.message for p in exc.problems]
    accepted = not problems
    if case["category"] == "faithful":
        ok = accepted
    elif case["category"] == "known_gap":
        ok = True
    else:
        reason = case["expect_problem"] or ""
        ok = not accepted and any(reason in p for p in problems)
    return Outcome(str(case["id"]), str(case["category"]), accepted, problems, ok)


def main() -> int:
    cases = [json.loads(line) for line in CASES_FILE.read_text().splitlines() if line.strip()]
    outcomes = [run_case(case) for case in cases]
    by_category: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        by_category.setdefault(outcome.category, []).append(outcome)

    print(f"Explanation grounding checks: {len(outcomes)} cases\n")
    labels = {
        "faithful": "Faithful wording accepted",
        "unfaithful": "Unfaithful wording rejected (right reason)",
        "malformed": "Malformed replies rejected",
        "known_gap": "Known gaps caught (not gated)",
    }
    for category, label in labels.items():
        group = by_category.get(category, [])
        if category == "known_gap":
            caught = sum(not o.accepted for o in group)
            print(f"  {label:46} {caught}/{len(group)}")
        else:
            print(f"  {label:46} {sum(o.ok for o in group)}/{len(group)}")

    failures = [o for o in outcomes if not o.ok]
    for o in failures:
        print(f"\n  FAILED {o.id} ({o.category}): accepted={o.accepted} {o.problems}")
    for o in by_category.get("known_gap", []):
        if o.accepted:
            print(f"  gap: {o.id} passes the checks")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
