"""The configured language model for the evaluations, from the same settings as the API."""

from app.ai.guard import GuardedLlm, UnlimitedBudget
from app.ai.provider import LlmProvider
from app.core.config import get_settings
from app.core.resilience import CircuitBreaker
from app.main import build_provider


def model_provider() -> LlmProvider:
    settings = get_settings()
    if not settings.uses_model:
        raise SystemExit(
            "Set LLM_API_KEY (and LLM_MODEL, LLM_BASE_URL if needed) to evaluate a model."
        )
    return build_provider(settings)


def model_llm() -> GuardedLlm:
    return GuardedLlm(
        model_provider(), UnlimitedBudget(), CircuitBreaker("eval", 1000, 1), timeout_s=60
    )
