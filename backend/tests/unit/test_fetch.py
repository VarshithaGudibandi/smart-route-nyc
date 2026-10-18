import httpx
import pytest

from app.ingestion.http import FetchError, fetch_bytes


class Server:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr("app.ingestion.http.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.ingestion.http.random.uniform", lambda a, b: b)
    return recorded


async def fetch(server: Server) -> bytes:
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as http:
        return await fetch_bytes(http, "https://feed.example/x")


async def test_retries_transient_errors_then_succeeds(sleeps: list[float]) -> None:
    server = Server(httpx.Response(503), httpx.Response(200, content=b"ok"))
    assert await fetch(server) == b"ok"
    assert server.calls == 2
    assert sleeps == [2.0]


async def test_honours_retry_after(sleeps: list[float]) -> None:
    server = Server(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200))
    await fetch(server)
    assert sleeps == [7.0]


async def test_does_not_retry_client_errors(sleeps: list[float]) -> None:
    server = Server(httpx.Response(404))
    with pytest.raises(FetchError) as info:
        await fetch(server)
    assert info.value.code == "http_404"
    assert server.calls == 1
    assert sleeps == []


async def test_gives_up_after_three_attempts(sleeps: list[float]) -> None:
    server = Server(httpx.ConnectTimeout("slow"))
    with pytest.raises(FetchError) as info:
        await fetch(server)
    assert info.value.code == "timeout"
    assert server.calls == 3
    assert sleeps == [2.0, 4.0]
