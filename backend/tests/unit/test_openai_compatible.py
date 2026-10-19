import json

import httpx
import pytest

from app.ai.guard import TransientLlmError
from app.ai.openai_compatible import OpenAICompatibleProvider
from app.ai.provider import LlmUnavailableError


def _provider(handler: httpx.MockTransport) -> OpenAICompatibleProvider:
    http = httpx.AsyncClient(base_url="https://llm.test/v1", transport=handler)
    return OpenAICompatibleProvider(http, "gpt-5-mini")


def _reply(content: str, finish_reason: str = "stop") -> dict[str, object]:
    return {
        "model": "gpt-5-mini-2026-08-07",
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 90, "completion_tokens": 30},
    }


async def test_sends_a_chat_completion_and_returns_the_text() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_reply('```json\n{"ok": true}\n```'))

    response = await _provider(httpx.MockTransport(handle)).complete_json("sys", "usr", 400)

    assert response.text == '{"ok": true}'
    assert (response.model, response.input_tokens, response.output_tokens) == (
        "gpt-5-mini-2026-08-07",
        90,
        30,
    )
    assert seen[0].url == "https://llm.test/v1/chat/completions"
    body = json.loads(seen[0].content)
    assert body["model"] == "gpt-5-mini"
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]


@pytest.mark.parametrize(("status", "error"), [(429, TransientLlmError), (503, TransientLlmError)])
async def test_rate_limits_and_server_errors_are_retried(status: int, error: type) -> None:
    provider = _provider(httpx.MockTransport(lambda _: httpx.Response(status)))
    with pytest.raises(error):
        await provider.complete_json("sys", "usr", 400)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"error": "bad key"}), "ai_rejected"),
        (httpx.Response(200, json=_reply('{"partial": ', "length")), "ai_truncated"),
        (httpx.Response(200, json=_reply("", "content_filter")), "ai_declined"),
        (httpx.Response(200, json={"unexpected": True}), "ai_unavailable"),
    ],
)
async def test_unusable_replies_are_unavailable(response: httpx.Response, code: str) -> None:
    provider = _provider(httpx.MockTransport(lambda _: response))
    with pytest.raises(LlmUnavailableError) as raised:
        await provider.complete_json("sys", "usr", 400)
    assert raised.value.code == code


async def test_no_credit_is_reported_rather_than_retried() -> None:
    # OpenAI's reply when the account has no credit.
    body = {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}
    provider = _provider(httpx.MockTransport(lambda _: httpx.Response(429, json=body)))
    with pytest.raises(LlmUnavailableError) as raised:
        await provider.complete_json("sys", "usr", 400)
    assert raised.value.code == "ai_no_credit"


async def test_errors_name_the_status_and_code_but_not_the_message() -> None:
    body = {"error": {"message": "secret details", "code": "rate_limit_exceeded"}}
    provider = _provider(httpx.MockTransport(lambda _: httpx.Response(429, json=body)))
    with pytest.raises(TransientLlmError) as raised:
        await provider.complete_json("sys", "usr", 400)
    assert str(raised.value) == "HTTP 429 rate_limit_exceeded"
