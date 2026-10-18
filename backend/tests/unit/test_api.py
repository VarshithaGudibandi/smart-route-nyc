from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.main import create_app


class Echo(BaseModel):
    stars: int = Field(ge=1, le=5)
    comment: str


@pytest.fixture
def app() -> FastAPI:
    settings = Settings(
        _env_file=None,
        app_env="test",
        allowed_origins=["http://localhost:3000"],
        max_request_body_bytes=256,
    )
    app = create_app(settings)

    # Test-only endpoints exercising validation and error handling.
    @app.post("/test/echo")
    async def echo(body: Echo) -> Echo:
        return body

    @app.get("/test/crash")
    async def crash() -> None:
        raise RuntimeError("boom")

    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_live(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generates_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert len(response.headers["X-Request-ID"]) == 32


def test_propagates_valid_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "trace-12345678"})
    assert response.headers["X-Request-ID"] == "trace-12345678"


def test_not_found_uses_error_shape(client: TestClient) -> None:
    response = client.get("/api/v1/nope", headers={"X-Request-ID": "trace-12345678"})
    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "Not Found",
        "request_id": "trace-12345678",
        "details": None,
    }


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    secret = "my home is 40.7128,-74.0060"
    response = client.post("/test/echo", json={"stars": 9, "comment": secret})
    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "invalid_request"
    assert body["details"][0]["loc"] == ["body", "stars"]
    assert secret not in response.text


def test_rejects_oversized_body_by_content_length(client: TestClient) -> None:
    response = client.post("/test/echo", json={"stars": 3, "comment": "x" * 1000})
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"
    assert "X-Request-ID" in response.headers


def test_rejects_oversized_chunked_body(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"stars": 3, "comment": "'
        yield b"x" * 1000
        yield b'"}'

    response = client.post(
        "/test/echo", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_unhandled_error_hides_internals(client: TestClient) -> None:
    response = client.get("/test/crash")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert "boom" not in response.text
    assert body["request_id"]


def test_cors_allows_configured_origin_only(client: TestClient) -> None:
    preflight = {"Access-Control-Request-Method": "POST"}
    allowed = client.options(
        "/api/v1/health/live", headers={"Origin": "http://localhost:3000", **preflight}
    )
    denied = client.options(
        "/api/v1/health/live", headers={"Origin": "https://evil.example", **preflight}
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in denied.headers
