from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cache.redis import ResilientRedis
from app.core.config import Settings
from app.db.models import SuggestionOutcome
from app.ingestion.retention import RetentionJob, RetentionPolicy
from app.ingestion.runner import run_job
from app.integrations.otp.mapper import to_route
from app.schemas.suggestions import (
    InvestigationResult,
    RejectedProposal,
    TransferProposal,
    VerifiedSuggestion,
)
from app.services.suggestions.outcomes import OutcomeRecorder
from tests.suggestion_fakes import EXPRESS, LOCAL, C, itinerary

PROPOSAL = TransferProposal(first_routes=[EXPRESS], transfer_station=C, second_routes=[LOCAL])


def result() -> InvestigationResult:
    route = to_route(itinerary([(EXPRESS, "MTASBWY:A", C, 1, 5), (LOCAL, C, "MTASBWY:D", 7, 10)]))
    return InvestigationResult(
        applicable=True,
        generator="rules",
        suggestions=[
            VerifiedSuggestion(proposal=PROPOSAL, source="rules", route=route, saves_seconds=240)
        ],
        rejected=[
            RejectedProposal(proposal=PROPOSAL, source="model", code="not_faster", detail="x")
        ],
    )


async def test_outcomes_hold_lines_and_stations_only(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await OutcomeRecorder(engine)("graph-1", result())
    async with sessions() as session:
        rows = list(
            await session.scalars(select(SuggestionOutcome).order_by(SuggestionOutcome.outcome))
        )
    assert [(r.source, r.outcome, r.saves_seconds) for r in rows] == [
        ("model", "not_faster", None),
        ("rules", "verified", 240),
    ]
    assert rows[1].first_routes == EXPRESS and rows[1].transfer_station == C
    assert {c.name for c in SuggestionOutcome.__table__.columns}.isdisjoint(
        {"lat", "lon", "origin", "destination", "departure"}
    )


async def test_retention_removes_old_outcomes(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession], redis: ResilientRedis
) -> None:
    await OutcomeRecorder(engine)("graph-1", result())
    async with sessions.begin() as session:
        await session.execute(
            update(SuggestionOutcome)
            .where(SuggestionOutcome.outcome == "verified")
            .values(created_at=datetime.now(UTC) - timedelta(days=400))
        )
    policy = RetentionPolicy.from_settings(Settings(_env_file=None))
    assert await run_job(RetentionJob(sessions, policy), sessions, redis)
    async with sessions() as session:
        remaining = list(await session.scalars(select(SuggestionOutcome.outcome)))
    assert remaining == ["not_faster"]
