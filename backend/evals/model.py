"""Claude for the evaluations, configured from the same settings as the API."""

from app.ai.anthropic_provider import AnthropicProvider
from app.ai.guard import GuardedLlm, UnlimitedBudget
from app.core.config import get_settings
from app.core.resilience import CircuitBreaker


def claude_provider() -> AnthropicProvider:
    settings = get_settings()
    if settings.anthropic_api_key is None:
        raise SystemExit("Set ANTHROPIC_API_KEY to evaluate Claude.")
    return AnthropicProvider.create(
        settings.anthropic_api_key.get_secret_value(), settings.llm_model, settings.llm_effort
    )


def claude_llm() -> GuardedLlm:
    return GuardedLlm(
        claude_provider(), UnlimitedBudget(), CircuitBreaker("eval", 1000, 1), timeout_s=60
    )
