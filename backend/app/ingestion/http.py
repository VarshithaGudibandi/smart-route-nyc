"""HTTP fetching for feed ingestion.

Transient failures (timeouts, connection errors, 429 and 5xx) are retried
with full-jitter exponential backoff, honouring ``Retry-After``. Other client
errors (401, 403, 404) are not retried: repeating them cannot succeed and
only adds load on the provider.
"""

import asyncio
import random

import httpx
import structlog

logger = structlog.get_logger(__name__)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0


class FetchError(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


async def fetch_bytes(http: httpx.AsyncClient, url: str) -> bytes:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        retry_after: float | None = None
        try:
            response = await http.get(url)
        except httpx.TimeoutException as exc:
            error = FetchError(f"timeout fetching {url}", "timeout")
            cause: Exception = exc
        except httpx.TransportError as exc:
            error = FetchError(f"connection error fetching {url}", "connection_error")
            cause = exc
        else:
            if response.status_code == 200:
                return response.content
            error = FetchError(
                f"HTTP {response.status_code} from {url}", f"http_{response.status_code}"
            )
            cause = httpx.HTTPStatusError("", request=response.request, response=response)
            if response.status_code not in RETRYABLE_STATUS:
                raise error from cause
            retry_after = _retry_after(response)

        if attempt == MAX_ATTEMPTS:
            raise error from cause
        delay = random.uniform(0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt))
        if retry_after is not None:
            delay = min(BACKOFF_CAP_S, max(delay, retry_after))
        logger.warning(
            "fetch_retry", url=url, attempt=attempt, code=error.code, delay_s=round(delay, 2)
        )
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is not None and value.isdigit():
        return float(value)
    return None
