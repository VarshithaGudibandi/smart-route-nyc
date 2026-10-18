"""Keyword-rule baseline: a deterministic, non-AI stand-in for a language model.

It answers through the same interface and schema as a real provider, which
makes it useful for local development and, above all, as the bar a model has
to beat in the evaluation. It is never presented as AI: responses report
provider ``baseline`` and model ``keyword-rules-v1``.
"""

import json
import re

from app.ai.provider import LlmResponse

_RIDER_TEXT = re.compile(r"<rider_text>\n(.*)\n</rider_text>", re.DOTALL)
_WHEELCHAIR = re.compile(r"\b(wheel ?chairs?|mobility scooter|power chair)\b")
_STEP_FREE = re.compile(
    r"\b(stairs?|steps|stroller|pram|buggy|walker|crutches|luggage|suitcases?|elevators? only|"
    r"step[- ]free|knee|hip)\b"
)
_STAIRS_OK = re.compile(r"\b(stairs|steps) (are|is) (fine|ok|okay)\b|\bdon'?t mind (the )?stairs\b")
_FEWEST_TRANSFERS = re.compile(
    r"\b(fewest|fewer|minimal|no|least|avoid) (transfers?|changes)\b|\bone[- ]seat\b"
)
_LESS_WALKING = re.compile(
    r"\b(less|minimal|least|little|minimum) walking\b|\bwalk (less|as little)\b|"
    r"\bcan'?t walk (far|much|long)\b"
)
_FASTEST = re.compile(r"\b(fastest|quickest|asap|in a hurry|running late)\b")
_WALK_LIMIT = re.compile(
    r"\b(\d{1,4}) ?(m|meters?|metres?|blocks?|minutes?|mins?|miles?)\b(?=[^.]*\bwalk)|"
    r"\bwalk[^.]*?\b(\d{1,4}) ?(m|meters?|metres?|blocks?|minutes?|mins?|miles?)\b"
)
_UNSUPPORTED = {
    "crowding": re.compile(r"\bcrowd(ed|s)?\b|\bbusy\b"),
    "cost": re.compile(r"\b(cheap(est)?|cost|fare|price)\b"),
    "safety": re.compile(r"\bsafe(st|ty)?\b"),
    "air conditioning": re.compile(r"\bair[- ]?condition"),
    "restrooms": re.compile(r"\b(bathroom|restroom|toilet)s?\b"),
}
_METERS_PER_UNIT = {
    "m": 1,
    "meter": 1,
    "metre": 1,
    "block": 80,
    "minute": 80,
    "min": 80,
    "mile": 1609,
}


class KeywordBaselineProvider:
    name = "baseline"
    model = "keyword-rules-v1"

    async def complete_json(self, system: str, user: str, max_tokens: int) -> LlmResponse:
        match = _RIDER_TEXT.search(user)
        text = (match.group(1) if match else user).lower()
        output: dict[str, object] = {}

        if _WHEELCHAIR.search(text):
            output["wheelchair_required"] = True
            output["step_free_required"] = True
        elif _STEP_FREE.search(text) and not _STAIRS_OK.search(text):
            output["step_free_required"] = True

        if _FEWEST_TRANSFERS.search(text):
            output["primary"] = "FEWEST_TRANSFERS"
            output["prefer_fewer_transfers"] = True
        if _LESS_WALKING.search(text):
            output["prefer_less_walking"] = True
            output.setdefault("primary", "LEAST_WALKING")
        if _FASTEST.search(text):
            output.setdefault("primary", "FASTEST")

        limit = _walk_limit(text)
        if limit is not None:
            output["max_walk_meters"] = limit

        unsupported = [label for label, pattern in _UNSUPPORTED.items() if pattern.search(text)]
        if unsupported:
            output["unsupported_requests"] = unsupported[:5]
        return LlmResponse(text=json.dumps(output), model=self.model)


def _walk_limit(text: str) -> int | None:
    match = _WALK_LIMIT.search(text)
    if match is None:
        return None
    number = match.group(1) or match.group(3)
    unit = (match.group(2) or match.group(4)).rstrip("s")
    meters = int(number) * _METERS_PER_UNIT.get(unit, 1)
    return min(5000, max(100, meters))
