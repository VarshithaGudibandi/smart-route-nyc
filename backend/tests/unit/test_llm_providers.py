from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.ai.anthropic_provider import THINKING_ALLOWANCE, AnthropicProvider
from app.ai.guard import TransientLlmError
from app.ai.provider import LlmUnavailableError
from app.core.config import Settings


class FakeMessages:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _provider(result: object) -> tuple[AnthropicProvider, FakeMessages]:
    messages = FakeMessages(result)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return AnthropicProvider(client, "claude-opus-5-5", "low"), messages  # type: ignore[arg-type]


def _message(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=120, output_tokens=40),
    )


async def test_returns_the_text_and_usage_and_sends_the_configured_request() -> None:
    provider, messages = _provider(_message('```json\n{"ok": true}\n```'))

    response = await provider.complete_json("system", "user", 400)

    assert response.text == '{"ok": true}'
    assert (response.model, response.input_tokens, response.output_tokens) == (
        "claude-opus-5-5",
        120,
        40,
    )
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 400 + THINKING_ALLOWANCE
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default"
    assert call["messages"] == [{"role": "user", "content": "user"}]


@pytest.mark.parametrize(
    ("stop_reason", "code"), [("refusal", "ai_declined"), ("max_tokens", "ai_truncated")]
)
async def test_unusable_replies_are_unavailable_not_parsed(stop_reason: str, code: str) -> None:
    provider, _ = _provider(_message('{"partial": ', stop_reason))
    with pytest.raises(LlmUnavailableError) as raised:
        await provider.complete_json("system", "user", 400)
    assert raised.value.code == code


async def test_connection_errors_are_retried_by_the_guard() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    provider, _ = _provider(anthropic.APIConnectionError(request=request))
    with pytest.raises(TransientLlmError):
        await provider.complete_json("system", "user", 400)


async def test_a_rejected_key_is_not_retried() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=request), body=None
    )
    provider, _ = _provider(error)
    with pytest.raises(LlmUnavailableError) as raised:
        await provider.complete_json("system", "user", 400)
    assert raised.value.code == "ai_rejected"


def test_auto_picks_the_provider_from_the_key() -> None:
    assert Settings(_env_file=None).resolved_llm_provider == "baseline"
    claude = Settings(_env_file=None, llm_api_key="sk-ant-test")
    assert (claude.resolved_llm_provider, claude.resolved_llm_model) == (
        "anthropic",
        "claude-opus-5-5",
    )
    other = Settings(_env_file=None, llm_api_key="sk-other", llm_model="gpt-5-mini")
    assert (other.resolved_llm_provider, other.resolved_llm_model) == ("openai", "gpt-5-mini")


def test_model_settings_must_be_complete() -> None:
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        Settings(_env_file=None, llm_provider="anthropic")
    with pytest.raises(ValueError, match="LLM_MODEL"):
        Settings(_env_file=None, llm_api_key="sk-other")


def test_an_empty_key_means_no_model() -> None:
    assert (
        Settings(_env_file=None, llm_api_key="", llm_model="").resolved_llm_provider == "baseline"
    )
