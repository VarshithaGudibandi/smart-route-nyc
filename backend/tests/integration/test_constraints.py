import hashlib
import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models import RouteFeedback


def ref_hash() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


async def test_feedback_stars_must_be_between_1_and_5(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session:
        session.add(RouteFeedback(route_result_ref_hash=ref_hash(), stars=6))
        with pytest.raises(IntegrityError, match="stars_range"):
            await session.commit()


async def test_feedback_comment_length_is_capped(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session:
        session.add(RouteFeedback(route_result_ref_hash=ref_hash(), stars=4, comment="x" * 1001))
        with pytest.raises(IntegrityError, match="comment_length"):
            await session.commit()


async def test_one_feedback_per_route_result(engine: AsyncEngine) -> None:
    shared = ref_hash()
    async with AsyncSession(engine) as session:
        session.add(RouteFeedback(route_result_ref_hash=shared, stars=5))
        await session.commit()
    async with AsyncSession(engine) as session:
        session.add(RouteFeedback(route_result_ref_hash=shared, stars=1))
        with pytest.raises(IntegrityError, match="uq_route_feedback_route_result_ref_hash"):
            await session.commit()
