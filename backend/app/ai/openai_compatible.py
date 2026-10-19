"""Any model served through the OpenAI-compatible chat completions API.

OpenAI, Google Gemini, Groq, Mistral, OpenRouter, Together and a local
Ollama all accept this request shape at their own base URL, so one class
covers them. As with every provider, callers validate the reply; the model
proposes and code decides.
"""

from typing import Any

import httpx

from app.ai.guard import TransientLlmError
from app.ai.provider import LlmResponse, LlmUnavailableError
from app.ai.text import strip_fence

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class OpenAICompatibleProvider:
    def __init__(self, http: httpx.AsyncClient, model: str) -> None:
        self._http = http
        self._model = model
        # Shown to riders ("Worded by gpt-5-mini") and part of cache keys.
        self.name = model

    @classmethod
    def create(cls, base_url: str, api_key: str, model: str) -> "OpenAICompatibleProvider":
        http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            # The guard owns the per-call timeout; this only bounds a hung socket.
            timeout=httpx.Timeout(60.0),
        )
        return cls(http, model)

    async def complete_json(self, system: str, user: str, max_tokens: int) -> LlmResponse:
        # No token limit is sent: providers disagree on its name and on whether
        # reasoning counts against it. Replies are short and validated anyway.
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = await self._http.post("/chat/completions", json=payload)
        except httpx.TransportError as exc:
            raise TransientLlmError(type(exc).__name__) from exc
        if response.status_code >= 400:
            code = _error_code(response)
            if code == "insufficient_quota":
                # OpenAI answers 429 when the account has no credit; waiting cannot help.
                raise LlmUnavailableError(
                    "The provider account has no credit (insufficient_quota)", "ai_no_credit"
                )
            status = " ".join(filter(None, [str(response.status_code), code]))
            if response.status_code in RETRYABLE_STATUS:
                raise TransientLlmError(f"HTTP {status}")
            # Bad key, unknown model, malformed request: retrying cannot help.
            raise LlmUnavailableError(
                f"The language model rejected the request ({status})", "ai_rejected"
            )
        try:
            body = response.json()
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmUnavailableError("Unexpected reply from the language model") from exc
        if choice.get("finish_reason") == "length":
            raise LlmUnavailableError("The language model's reply was cut off", "ai_truncated")
        if choice.get("finish_reason") == "content_filter":
            raise LlmUnavailableError("The language model declined the request", "ai_declined")
        usage = body.get("usage") or {}
        return LlmResponse(
            text=strip_fence(text),
            model=str(body.get("model") or self._model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    async def close(self) -> None:
        await self._http.aclose()


def _error_code(response: httpx.Response) -> str:
    """The provider's error code or type, such as ``insufficient_quota``; never its message."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return ""
    if isinstance(error, list):  # Gemini wraps errors in a list
        error = error[0] if error else {}
    if not isinstance(error, dict):
        return ""
    value = error.get("code") or error.get("type") or error.get("status") or ""
    return str(value)[:64]
