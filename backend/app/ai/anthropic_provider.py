"""Claude, through the Anthropic API.

The provider only turns a prompt into text. Callers validate that text
against strict schemas and discard anything that does not match, so the
model can propose but never decide.
"""

from typing import Literal

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam

from app.ai.guard import TransientLlmError
from app.ai.provider import LlmResponse, LlmUnavailableError

# If a request is declined by the safety classifiers, the API reruns it on a
# fallback model chosen for the decline's category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

Effort = Literal["low", "medium", "high"]
# Callers size max_tokens for the answer. The model also thinks before
# answering, and thinking counts against the same limit; only tokens actually
# generated are billed.
THINKING_ALLOWANCE = 8000


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic, model: str, effort: Effort) -> None:
        self._client = client
        self._model = model
        self._effort = effort

    @classmethod
    def create(cls, api_key: str, model: str, effort: Effort) -> "AnthropicProvider":
        # The guard retries once and owns the timeout; one SDK retry is enough.
        return cls(anthropic.AsyncAnthropic(api_key=api_key, max_retries=1), model, effort)

    async def complete_json(self, system: str, user: str, max_tokens: int) -> LlmResponse:
        messages: list[BetaMessageParam] = [{"role": "user", "content": user}]
        output_config: BetaOutputConfigParam = {"effort": self._effort}
        try:
            response = await self._client.beta.messages.create(
                model=self._model,
                max_tokens=max_tokens + THINKING_ALLOWANCE,
                system=system,
                messages=messages,
                output_config=output_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except (
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
        ) as exc:
            raise TransientLlmError(type(exc).__name__) from exc
        except anthropic.APIStatusError as exc:
            # Bad key, no access to the model, malformed request: retrying cannot help.
            raise LlmUnavailableError(
                f"The language model rejected the request ({exc.status_code})", "ai_rejected"
            ) from exc

        if response.stop_reason == "refusal":
            raise LlmUnavailableError("The language model declined the request", "ai_declined")
        if response.stop_reason == "max_tokens":
            raise LlmUnavailableError("The language model's reply was cut off", "ai_truncated")
        text = "".join(block.text for block in response.content if block.type == "text")
        return LlmResponse(
            text=_strip_fence(text),
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def _strip_fence(text: str) -> str:
    """Accept JSON wrapped in a Markdown code fence; the JSON is validated afterwards."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = stripped[3:-3]
        stripped = stripped.removeprefix("json")
    return stripped.strip()
