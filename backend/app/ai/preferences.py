"""Interpret a rider's own words as search preferences.

The model only proposes values; this module decides what is applied:

1. The text is cleaned (control characters removed, whitespace collapsed) and
   sent as data inside delimiters, never as instructions.
2. The reply must parse as ``ModelOutput`` (closed schema, allow-listed
   values). Anything else is rejected, not repaired.
3. The proposal is merged into the rider's current selection by fixed rules:
   text can add accessibility requirements and change preferences, but can
   never remove a requirement; fields the model marks uncertain are not
   applied. Wheelchair use implies step-free travel.
4. The rider reviews the result before anything is used for a search.

If the model fails (no credit, bad key, outage, an unusable reply), the
keyword baseline answers instead and the response says so, so typing a need
never dead-ends.

Validated model answers are cached in Redis by an HMAC of the cleaned text,
so repeated phrasings do not spend tokens.
"""

import hashlib
import hmac
import json
import re
from typing import Any

import structlog
from pydantic import ValidationError

from app.ai.baseline import KeywordBaselineProvider
from app.ai.guard import GuardedLlm
from app.ai.prompts import (
    PREFERENCES_PROMPT_VERSION,
    PREFERENCES_SYSTEM,
    preferences_user_message,
)
from app.ai.provider import DisabledProvider, LlmUnavailableError
from app.cache.redis import ResilientRedis
from app.schemas.preferences import (
    Conflict,
    FieldChange,
    InterpretationSource,
    InterpretResponse,
    ModelOutput,
    PreferenceField,
    PreferenceState,
)

logger = structlog.get_logger(__name__)

MAX_OUTPUT_TOKENS = 400
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

ACCESSIBILITY_FLAGS: tuple[PreferenceField, ...] = ("step_free_required", "wheelchair_required")
PREFERENCE_FIELDS: tuple[PreferenceField, ...] = (
    "primary",
    "prefer_fewer_transfers",
    "prefer_less_walking",
)


class InvalidModelOutputError(Exception):
    """The model's reply was not a valid ``ModelOutput``."""


def clean_text(text: str) -> str:
    return _WHITESPACE.sub(" ", _CONTROL_CHARS.sub(" ", text)).strip()


class PreferenceInterpreter:
    def __init__(
        self,
        llm: GuardedLlm,
        cache: ResilientRedis | None,
        secret: bytes,
        cache_ttl_s: int,
    ) -> None:
        self._llm = llm
        self._cache = cache
        self._secret = secret
        self._cache_ttl_s = cache_ttl_s

    async def interpret(self, text: str, current: PreferenceState) -> InterpretResponse:
        cleaned = clean_text(text)
        key = self._cache_key(cleaned)
        provider = self._llm.provider.name
        cached = await self._read_cache(key)
        if cached is not None:
            output, model = cached
        else:
            try:
                response = await self._llm.complete_json(
                    "preferences",
                    PREFERENCES_SYSTEM,
                    preferences_user_message(cleaned),
                    MAX_OUTPUT_TOKENS,
                )
                output = parse_model_output(response.text)
                model = response.model
                await self._write_cache(key, output, model)
            except (LlmUnavailableError, InvalidModelOutputError) as exc:
                # A deployment that turned typed needs off keeps them off.
                if provider in (KeywordBaselineProvider.name, DisabledProvider.name):
                    raise
                logger.warning(
                    "preferences_model_failed",
                    error=type(exc).__name__,
                    reason=getattr(exc, "code", None),
                )
                output, model = await _baseline(cleaned)
                provider = KeywordBaselineProvider.name

        source = InterpretationSource(
            provider=provider,
            model=model,
            prompt_version=PREFERENCES_PROMPT_VERSION,
            cached=cached is not None,
        )
        return merge(current, output, source)

    def _cache_key(self, cleaned: str) -> str:
        material = f"{PREFERENCES_PROMPT_VERSION}|{self._llm.provider.name}|{cleaned.lower()}"
        digest = hmac.new(self._secret, material.encode(), hashlib.sha256).hexdigest()[:32]
        return f"srn:v1:prefs:{digest}"

    async def _read_cache(self, key: str) -> tuple[ModelOutput, str] | None:
        if self._cache is None:
            return None
        raw = await self._cache.get(key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            return ModelOutput.model_validate(payload["output"]), str(payload["model"])
        except (ValueError, KeyError, ValidationError):
            return None

    async def _write_cache(self, key: str, output: ModelOutput, model: str) -> None:
        if self._cache is None:
            return
        payload = {"output": output.model_dump(), "model": model}
        await self._cache.set(key, json.dumps(payload).encode(), ttl_s=self._cache_ttl_s)


async def _baseline(cleaned: str) -> tuple[ModelOutput, str]:
    baseline = KeywordBaselineProvider()
    reply = await baseline.complete_json(
        PREFERENCES_SYSTEM, preferences_user_message(cleaned), MAX_OUTPUT_TOKENS
    )
    return parse_model_output(reply.text), baseline.model


def parse_model_output(text: str) -> ModelOutput:
    """Strictly parse a model reply. Unknown fields or values are rejected."""
    try:
        return ModelOutput.model_validate_json(text.strip())
    except ValidationError as exc:
        logger.warning("llm_output_invalid", errors=exc.error_count())
        raise InvalidModelOutputError("The model's reply did not match the schema") from exc


def merge(
    current: PreferenceState, output: ModelOutput, source: InterpretationSource
) -> InterpretResponse:
    accessibility = current.accessibility.model_dump()
    optimization = current.optimization.model_dump()
    uncertain = set(output.uncertain_fields)
    changes: list[FieldChange] = []
    conflicts: list[Conflict] = []

    def interpreted(field: PreferenceField) -> Any:
        return None if field in uncertain else getattr(output, field)

    for field in ACCESSIBILITY_FLAGS:
        value = interpreted(field)
        if value is True and not accessibility[field]:
            changes.append(FieldChange(field=field, previous=False, proposed=True))
            accessibility[field] = True
        elif value is False and accessibility[field]:
            conflicts.append(
                Conflict(
                    field=field,
                    current=True,
                    interpreted=False,
                    resolution="kept_current",
                    message="Requirements you selected are only removed by you, not by text.",
                )
            )

    # Someone who uses a wheelchair also needs step-free travel.
    if accessibility["wheelchair_required"] and not accessibility["step_free_required"]:
        changes.append(FieldChange(field="step_free_required", previous=False, proposed=True))
        accessibility["step_free_required"] = True

    walk = interpreted("max_walk_meters")
    previous_walk = accessibility["max_walk_meters"]
    if walk is not None and walk != previous_walk:
        if previous_walk is None or walk < previous_walk:
            changes.append(
                FieldChange(field="max_walk_meters", previous=previous_walk, proposed=walk)
            )
            accessibility["max_walk_meters"] = walk
        else:
            conflicts.append(
                Conflict(
                    field="max_walk_meters",
                    current=previous_walk,
                    interpreted=walk,
                    resolution="kept_current",
                    message="Your stricter walking limit is kept.",
                )
            )

    for field in PREFERENCE_FIELDS:
        value = interpreted(field)
        if value is not None and value != optimization[field]:
            changes.append(FieldChange(field=field, previous=optimization[field], proposed=value))
            optimization[field] = value

    return InterpretResponse(
        proposed=PreferenceState.model_validate(
            {"accessibility": accessibility, "optimization": optimization}
        ),
        changes=changes,
        conflicts=conflicts,
        uncertain_fields=sorted(uncertain),
        unsupported_requests=list(output.unsupported_requests),
        source=source,
    )
