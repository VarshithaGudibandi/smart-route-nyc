"""Transparency about the data behind every answer."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Resources, get_resources
from app.core.freshness import SourceKind, classify
from app.db.models import SourceFreshness
from app.schemas.freshness import DataFreshnessResponse, SourceFreshnessItem

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/freshness")
async def data_freshness(
    resources: Annotated[Resources, Depends(get_resources)],
) -> DataFreshnessResponse:
    """How current each upstream source is, classified at request time."""
    now = datetime.now(UTC)
    async with AsyncSession(resources.db_engine) as session:
        rows = (
            await session.scalars(select(SourceFreshness).order_by(SourceFreshness.source_key))
        ).all()

    items = []
    for row in rows:
        kind = SourceKind(row.kind)
        items.append(
            SourceFreshnessItem(
                source=row.source_key,
                kind=kind,
                freshness=classify(kind, row.observed_at, row.valid_until, now),
                last_fetch=row.current_status,
                observed_at=row.observed_at,
                fetched_at=row.fetched_at,
                valid_until=row.valid_until,
            )
        )
    return DataFreshnessResponse(generated_at=now, sources=items)
