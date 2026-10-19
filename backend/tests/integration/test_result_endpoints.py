import dataclasses
import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.graph_version import GraphVersion
from app.db.models import RouteFeedback
from app.main import create_app
from app.services.cache_keys import NEW_YORK
from app.services.route_search import RouteSearchService
from tests.fakes import FakeOtpServer, StaticSnapshots, fake_otp

FIXTURE_DAY = "2026-10-19"


@pytest.fixture
def server() -> FakeOtpServer:
    """Recorded plan moved to two days ahead, so searches are never in the past."""
    server = FakeOtpServer()
    day = (datetime.now(NEW_YORK) + timedelta(days=2)).date()
    server.payload = json.loads(json.dumps(server.payload).replace(FIXTURE_DAY, day.isoformat()))
    return server


@pytest.fixture
def client(
    migrated_postgres_url: str,
    redis_url: str,
    sessions: async_sessionmaker[AsyncSession],
    server: FakeOtpServer,
) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        app_env="test",
        redis_url=redis_url,
        database_url=migrated_postgres_url,
        key_hashing_secret=uuid.uuid4().hex,
        suggestions_enabled=False,
        rate_limit_feedback_per_minute=4,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        res = app.state.resources
        otp = fake_otp(server.handler)
        route_search = RouteSearchService(
            otp, res.redis, GraphVersion(None), StaticSnapshots(), res.settings
        )
        app.state.resources = dataclasses.replace(res, otp=otp, route_search=route_search)
        yield client


def search(client: TestClient) -> dict[str, Any]:
    day = (datetime.now(NEW_YORK) + timedelta(days=2)).date()
    depart = datetime.combine(day, datetime.min.time(), NEW_YORK).replace(hour=8, minute=30)
    response = client.post(
        "/api/v1/routes/search",
        json={
            "origin": {"lat": 40.73301, "lon": -74.06289},
            "destination": {"lat": 40.80755, "lon": -73.96449},
            "depart_at": depart.isoformat(),
        },
    )
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload["routes"]
    return payload


async def stored(sessions: async_sessionmaker[AsyncSession]) -> list[RouteFeedback]:
    async with sessions() as session:
        return list(await session.scalars(select(RouteFeedback)))


async def test_feedback_is_stored_anonymously_and_a_second_rating_replaces_the_first(
    client: TestClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    result = search(client)
    body = {
        "search_id": result["search_id"],
        "route_id": result["routes"][0]["route_id"],
        "stars": 2,
        "comment": "Too crowded. Reach me at someone@example.com",
    }
    first = client.post("/api/v1/feedback", json=body)
    second = client.post("/api/v1/feedback", json=body | {"stars": 4, "comment": None})

    assert first.status_code == second.status_code == 201
    assert (first.json()["status"], second.json()["status"]) == ("recorded", "updated")
    assert first.json()["feedback_id"] == second.json()["feedback_id"]
    rows = await stored(sessions)
    assert len(rows) == 1 and rows[0].stars == 4 and rows[0].comment is None
    # The reference is an HMAC; the search and route IDs are not stored as such.
    assert result["search_id"] not in rows[0].route_result_ref_hash


async def test_comment_contact_details_are_masked(
    client: TestClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    result = search(client)
    client.post(
        "/api/v1/feedback",
        json={
            "search_id": result["search_id"],
            "route_id": result["routes"][1]["route_id"],
            "stars": 5,
            "comment": "Perfect. Text me on 646-555-0123",
        },
    )
    assert [r.comment for r in await stored(sessions)] == ["Perfect. Text me on [number removed]"]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"search_id": "0" * 32}, 404),  # never issued or expired
        ({"route_id": "0" * 16}, 404),  # not a route of that search
        ({"stars": 6}, 422),
        ({"stars": "5"}, 422),
        ({"comment": "x" * 1001}, 422),
        ({"extra": True}, 422),
    ],
)
def test_invalid_feedback_is_rejected(
    client: TestClient, overrides: dict[str, Any], code: int
) -> None:
    result = search(client)
    body = {
        "search_id": result["search_id"],
        "route_id": result["routes"][0]["route_id"],
        "stars": 3,
    }
    assert client.post("/api/v1/feedback", json=body | overrides).status_code == code


def test_feedback_is_rate_limited(client: TestClient) -> None:
    result = search(client)
    body = {"search_id": result["search_id"], "route_id": result["routes"][0]["route_id"]}
    statuses = [
        client.post("/api/v1/feedback", json=body | {"stars": 1 + i % 5}).status_code
        for i in range(6)
    ]
    assert 429 in statuses and statuses[0] == 201


def test_explanation_cites_server_side_facts(client: TestClient) -> None:
    result = search(client)
    route = result["routes"][0]
    response = client.post(
        "/api/v1/routes/explain",
        json={"search_id": result["search_id"], "route_id": route["route_id"]},
    )
    assert response.status_code == 200
    explanation = response.json()
    assert explanation["source"] == "facts"
    fact_ids = {f["id"] for f in explanation["facts"]}
    assert {"summary", "accessibility"} <= fact_ids
    assert all(set(s["fact_ids"]) <= fact_ids for s in explanation["sentences"])
    # PATH publishes no elevator data: the explanation must say so.
    assert any("PATH publishes no elevator data" in f["text"] for f in explanation["facts"])


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"search_id": "0" * 32, "route_id": "0" * 16}, 404),
        ({"search_id": "not-hex", "route_id": "0" * 16}, 422),
        ({"route": {"legs": []}}, 422),  # routes cannot be supplied by the client
    ],
)
def test_explanation_only_for_issued_results(
    client: TestClient, body: dict[str, Any], code: int
) -> None:
    assert client.post("/api/v1/routes/explain", json=body).status_code == code
