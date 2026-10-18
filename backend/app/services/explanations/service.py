"""Explain a route from a recent search.

1. Look the route up by ``search_id`` and ``route_id``: only routes this
   server returned can be explained.
2. Re-assess its accessibility with current elevator data, as every search
   response does, so an explanation never repeats an older verdict.
3. Build the facts. Without a model, the explanation is the facts.
4. With a model: ask it to word the facts, then check every sentence
   against them (``app.ai.explanations``). A reply that fails any check is
   discarded and the facts are shown, with a notice saying so.

Checked model wording is cached by an HMAC of the facts themselves, so a
change in any fact (an elevator status, an alert) gets a new explanation.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime

import structlog
from pydantic import ValidationError

from app.ai.explanations import (
    InvalidExplanationError,
    check_grounding,
    parse_explanation,
)
from app.ai.guard import GuardedLlm
from app.ai.prompts import (
    EXPLANATIONS_PROMPT_VERSION,
    EXPLANATIONS_SYSTEM,
    explanations_user_message,
)
from app.ai.provider import LlmUnavailableError
from app.cache.redis import ResilientRedis
from app.core.errors import AppError, NotFoundError
from app.schemas.explanations import (
    ExplanationFact,
    ExplanationResponse,
    ExplanationSentence,
    ModelExplanation,
)
from app.services.accessibility.assessor import assess_route
from app.services.accessibility.snapshot import SnapshotProvider
from app.services.explanations.facts import Fact, route_facts
from app.services.search_results import SearchResults
from app.services.suggestions.service import AlertSource

logger = structlog.get_logger(__name__)

MAX_OUTPUT_TOKENS = 900


class ExplanationService:
    def __init__(
        self,
        results: SearchResults,
        accessibility: SnapshotProvider,
        alerts: AlertSource,
        llm: GuardedLlm | None,
        cache: ResilientRedis,
        secret: bytes,
        cache_ttl_s: int,
    ) -> None:
        self._results = results
        self._accessibility = accessibility
        self._alerts = alerts
        self._llm = llm
        self._cache = cache
        self._secret = secret
        self._cache_ttl_s = cache_ttl_s

    async def explain(self, search_id: str, route_id: str) -> ExplanationResponse:
        found = await self._results.route(search_id, route_id)
        if found is None:
            raise NotFoundError("This route result is no longer available; search again")
        stored, route = found

        now = datetime.now(UTC)
        snapshot = await self._accessibility.current()
        route = assess_route(route, snapshot, stored.wheelchair_required, now)
        facts = route_facts(
            route, stored.accessibility_required, snapshot, await self._alerts.current(), now
        )
        response = ExplanationResponse(
            search_id=search_id,
            route_id=route_id,
            source="facts",
            sentences=[ExplanationSentence(text=f.text, fact_ids=[f.id]) for f in facts],
            facts=[ExplanationFact(**f.model_dump()) for f in facts],
        )
        if self._llm is None:
            return response

        try:
            explanation, cached = await self._worded(facts)
        except InvalidExplanationError as exc:
            logger.warning("explanation_rejected", problems=str(exc)[:500])
            return response.model_copy(
                update={
                    "notice": "The generated wording did not match the route facts, "
                    "so the facts are shown as they are."
                }
            )
        except (LlmUnavailableError, AppError) as exc:
            logger.info("explanation_model_unavailable", error=type(exc).__name__)
            return response
        return response.model_copy(
            update={
                "source": "model",
                "provider": self._llm.provider.name,
                "prompt_version": EXPLANATIONS_PROMPT_VERSION,
                "cached": cached,
                "sentences": [
                    ExplanationSentence(text=s.text, fact_ids=s.fact_ids)
                    for s in explanation.sentences
                ],
            }
        )

    async def _worded(self, facts: list[Fact]) -> tuple[ModelExplanation, bool]:
        assert self._llm is not None
        document = json.dumps([f.model_dump() for f in facts], separators=(",", ":"))
        key = self._cache_key(document)
        if (raw := await self._cache.get(key)) is not None:
            try:
                return ModelExplanation.model_validate_json(raw), True
            except ValidationError:
                pass
        reply = await self._llm.complete_json(
            "explanations",
            EXPLANATIONS_SYSTEM,
            explanations_user_message(document),
            MAX_OUTPUT_TOKENS,
        )
        explanation = parse_explanation(reply.text)
        if problems := check_grounding(explanation, facts):
            raise InvalidExplanationError(problems)
        await self._cache.set(key, explanation.model_dump_json().encode(), ttl_s=self._cache_ttl_s)
        return explanation, False

    def _cache_key(self, document: str) -> str:
        assert self._llm is not None
        material = f"{EXPLANATIONS_PROMPT_VERSION}|{self._llm.provider.name}|{document}"
        digest = hmac.new(self._secret, material.encode(), hashlib.sha256).hexdigest()[:32]
        return f"srn:v1:explanation:{digest}"
