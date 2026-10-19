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

``--live N`` also sends the scenario's facts to Claude N times, exactly as
the API does, and reports how many replies pass the checks and why the
others were rejected (needs ANTHROPIC_API_KEY).
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.ai.explanations import InvalidExplanationError, check_grounding, parse_explanation
from app.ai.prompts import EXPLANATIONS_SYSTEM, explanations_user_message
from app.ai.provider import LlmUnavailableError
from app.services.explanations.service import MAX_OUTPUT_TOKENS
from evals.explanation_scenario import scenario_facts
from evals.model import claude_llm

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


async def live(runs: int) -> None:
    """Score Claude's own wording of the scenario's facts."""
    llm = claude_llm()
    facts = scenario_facts()
    document = json.dumps([f.model_dump() for f in facts], separators=(",", ":"))
    accepted = 0
    reasons: Counter[str] = Counter()
    for _ in range(runs):
        try:
            reply = await llm.complete_json(
                "explanations",
                EXPLANATIONS_SYSTEM,
                explanations_user_message(document),
                MAX_OUTPUT_TOKENS,
            )
            problems = [p.message for p in check_grounding(parse_explanation(reply.text), facts)]
        except InvalidExplanationError as exc:
            problems = ["schema: " + p.message for p in exc.problems]
        except LlmUnavailableError as exc:
            problems = [f"unavailable: {exc.code}"]
        if problems:
            reasons.update(p.split(":")[0] for p in problems)
        else:
            accepted += 1
    print(f"\nClaude ({llm.provider.name}), {runs} replies: {accepted} passed the checks")
    for reason, count in reasons.most_common():
        print(f"  rejected for {reason}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--live", type=int, metavar="N", help="also score N replies from Claude")
    args = parser.parse_args()
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
    if args.live:
        asyncio.run(live(args.live))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
