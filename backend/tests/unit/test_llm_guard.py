import asyncio

import pytest

from app.ai.guard import GuardedLlm, TransientLlmError, UnlimitedBudget
from app.ai.provider import LlmResponse, LlmUnavailableError
from app.core.resilience import CircuitBreaker


class ScriptedProvider:
    """Plays back a sequence of replies or exceptions."""

    name = "scripted"

    def __init__(self, *script: str | Exception, delay_s: float = 0) -> None:
        self.script = list(script)
        self.calls = 0
        self.delay_s = delay_s

    async def complete_json(self, system: str, user: str, max_tokens: int) -> LlmResponse:
        self.calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return LlmResponse(text=item, model="scripted-1", input_tokens=10, output_tokens=5)


class ExhaustedBudget:
    async def spend(self) -> None:
        raise LlmUnavailableError("over budget", "ai_budget_exhausted")


def guard(provider: ScriptedProvider, threshold: int = 5, timeout_s: float = 1) -> GuardedLlm:
    return GuardedLlm(provider, UnlimitedBudget(), CircuitBreaker("llm", threshold, 60), timeout_s)


async def test_returns_reply() -> None:
    reply = await guard(ScriptedProvider("{}")).complete_json("t", "s", "u", 10)
    assert reply.text == "{}"


async def test_retries_one_transient_error() -> None:
    provider = ScriptedProvider(TransientLlmError("429"), "{}")
    assert (await guard(provider).complete_json("t", "s", "u", 10)).text == "{}"
    assert provider.calls == 2


async def test_timeout_becomes_unavailable() -> None:
    provider = ScriptedProvider("{}", delay_s=0.2)
    with pytest.raises(LlmUnavailableError):
        await guard(provider, timeout_s=0.05).complete_json("t", "s", "u", 10)
    assert provider.calls == 2


async def test_open_breaker_stops_calls() -> None:
    provider = ScriptedProvider(TransientLlmError("503"))
    llm = guard(provider, threshold=2)
    with pytest.raises(LlmUnavailableError):
        await llm.complete_json("t", "s", "u", 10)
    calls = provider.calls
    with pytest.raises(LlmUnavailableError):
        await llm.complete_json("t", "s", "u", 10)
    assert provider.calls == calls


async def test_budget_is_checked_before_calling() -> None:
    provider = ScriptedProvider("{}")
    llm = GuardedLlm(provider, ExhaustedBudget(), CircuitBreaker("llm", 5, 60), 1)
    with pytest.raises(LlmUnavailableError) as info:
        await llm.complete_json("t", "s", "u", 10)
    assert info.value.code == "ai_budget_exhausted"
    assert provider.calls == 0
