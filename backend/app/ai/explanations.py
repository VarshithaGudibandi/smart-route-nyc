"""Check a model's wording of a route explanation against the facts.

The model may rephrase facts; it may not add to them. Its reply must be a
``ModelExplanation`` whose sentences each cite fact IDs, and every sentence
must pass deterministic checks against the facts it cites:

* cited IDs exist;
* every number and clock time in the sentence appears in those facts;
* every capitalised name (stations, lines, agencies) appears in them;
* accessibility and certainty words ("accessible", "elevator", "stairs",
  "guaranteed", "fastest", ...) appear in them, so an ``unknown`` can never
  be worded as "accessible";
* across all sentences, every required fact (barriers, unconfirmed steps,
  alerts, stale data) is cited.

These checks are lexical. They reject some faithful paraphrases, which
costs only the nicer wording. They also have a known gap: a number is
checked for presence in the cited facts, not for meaning, so "about 1
hour" passes when a cited fact mentions "1 transfer". Stations, lines,
accessibility claims and omissions are the failures that matter most for
riders, and those are checked by name and by word.
"""

import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.schemas.explanations import ModelExplanation
from app.services.explanations.facts import Fact

_NUMBER = re.compile(r"\d+(?::\d{2})?")
_NAME = re.compile(r"\b[A-Z][\w'\u2019-]*")
_WORD = re.compile(r"[a-z][a-z-]*")
# Words that turn a description into a claim; they must come from the facts.
CLAIM_WORDS = frozenset(
    {
        "accessible",
        "inaccessible",
        "step-free",
        "elevator",
        "elevators",
        "escalator",
        "escalators",
        "lift",
        "lifts",
        "ramp",
        "ramps",
        "stairs",
        "steps",
        "wheelchair",
        "barrier",
        "barriers",
        "guaranteed",
        "guarantee",
        "always",
        "never",
        "safe",
        "safest",
        "fastest",
        "quickest",
        "best",
        "crowded",
        "delay",
        "delays",
        "delayed",
        "suspended",
        "live",
        "realtime",
        "real-time",
    }
)
# Capitalised only because they start a sentence.
SENTENCE_STARTERS = frozenset(
    {
        "Take", "Ride", "Walk", "Change", "Transfer", "Leave", "Leaves", "Arrive",
        "Arrives", "Then", "Next", "After", "Before", "From", "At", "In", "On", "This",
        "These", "The", "Your", "You", "It", "There", "Every", "Some", "All", "One",
        "Times", "Note", "Expect", "Board", "Get", "Exit", "Allow", "Please", "Both",
        "A", "An", "Once", "If", "Because", "So", "But", "And", "Finally", "First",
    }
)  # fmt: skip


@dataclass(frozen=True)
class GroundingProblem:
    sentence: int
    message: str


class InvalidExplanationError(Exception):
    def __init__(self, problems: list[GroundingProblem]) -> None:
        super().__init__("; ".join(f"sentence {p.sentence}: {p.message}" for p in problems))
        self.problems = problems


def parse_explanation(text: str) -> ModelExplanation:
    try:
        return ModelExplanation.model_validate_json(text.strip())
    except ValidationError as exc:
        raise InvalidExplanationError(
            [GroundingProblem(0, "reply does not match the schema")]
        ) from exc


def check_grounding(explanation: ModelExplanation, facts: list[Fact]) -> list[GroundingProblem]:
    by_id = {fact.id: fact for fact in facts}
    problems: list[GroundingProblem] = []
    cited: set[str] = set()

    for number, sentence in enumerate(explanation.sentences, start=1):
        unknown = [i for i in sentence.fact_ids if i not in by_id]
        if unknown:
            problems.append(GroundingProblem(number, f"cites unknown facts {unknown}"))
            continue
        cited.update(sentence.fact_ids)
        source = " ".join(by_id[i].text for i in sentence.fact_ids)
        source_words = set(_WORD.findall(source.lower()))

        if extra := sorted(set(_NUMBER.findall(sentence.text)) - set(_NUMBER.findall(source))):
            problems.append(GroundingProblem(number, f"numbers not in its facts: {extra}"))
        names = _NAME.findall(sentence.text)
        if names and names[0] in SENTENCE_STARTERS and sentence.text.startswith(names[0]):
            names = names[1:]
        source_names = set(_NAME.findall(source))
        if extra := sorted(set(names) - source_names):
            problems.append(GroundingProblem(number, f"names not in its facts: {extra}"))
        claims = CLAIM_WORDS & set(_WORD.findall(sentence.text.lower()))
        if extra := sorted(claims - source_words):
            problems.append(GroundingProblem(number, f"claims not in its facts: {extra}"))

    missing = [fact.id for fact in facts if fact.required and fact.id not in cited]
    if missing:
        problems.append(GroundingProblem(0, f"leaves out required facts {missing}"))
    return problems
