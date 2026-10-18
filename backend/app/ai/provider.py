"""Language-model provider interface.

Providers turn a system prompt and user content into text that should be a
JSON document. Everything else (timeouts, retries, budgets, validation)
lives outside the provider, so swapping vendors means writing one class.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LlmResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LlmProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def complete_json(self, system: str, user: str, max_tokens: int) -> LlmResponse:
        """Return the model's reply, which the caller validates as JSON."""
        ...


class LlmUnavailableError(Exception):
    """No model can answer right now; callers fall back to manual controls."""

    def __init__(self, message: str, code: str = "ai_unavailable") -> None:
        super().__init__(message)
        self.code = code


class DisabledProvider:
    """Used when no provider is configured."""

    name = "disabled"

    async def complete_json(self, system: str, user: str, max_tokens: int) -> LlmResponse:
        raise LlmUnavailableError("No language model is configured", "ai_not_configured")
