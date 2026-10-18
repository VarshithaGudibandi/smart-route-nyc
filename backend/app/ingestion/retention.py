"""Data retention: delete what the product no longer needs.

Runs in small batches so a large backlog never holds long locks. The
application stores no accounts or travel histories; this bounds the
operational records it does keep.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

from app.core.config import Settings
from app.db.models import AccessibilityEvent, FeedSyncRun, RouteFeedback, SuggestionOutcome
from app.ingestion.runs import RunContext, RunOutcome

BATCH_SIZE = 5000


@dataclass
class RetentionPolicy:
    events: timedelta
    sync_runs: timedelta
    feedback_comments: timedelta
    feedback: timedelta
    suggestion_outcomes: timedelta

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetentionPolicy":
        return cls(
            events=timedelta(days=settings.retention_events_days),
            sync_runs=timedelta(days=settings.retention_sync_runs_days),
            feedback_comments=timedelta(days=settings.retention_feedback_comment_days),
            feedback=timedelta(days=settings.retention_feedback_days),
            suggestion_outcomes=timedelta(days=settings.retention_suggestion_outcomes_days),
        )


class RetentionJob:
    provider = "smartroute"
    source_key = "retention"
    kind = None

    def __init__(self, sessions: async_sessionmaker[AsyncSession], policy: RetentionPolicy) -> None:
        self._sessions = sessions
        self._policy = policy

    async def run(self, ctx: RunContext) -> RunOutcome:
        now = datetime.now(UTC)
        p = self._policy
        events = await self._batched_delete(
            AccessibilityEvent.id, AccessibilityEvent.fetched_at < now - p.events
        )
        runs = await self._batched_delete(
            FeedSyncRun.run_id,
            (FeedSyncRun.started_at < now - p.sync_runs) & (FeedSyncRun.run_id != ctx.run_id),
        )
        feedback = await self._batched_delete(
            RouteFeedback.feedback_id, RouteFeedback.created_at < now - p.feedback
        )
        outcomes = await self._batched_delete(
            SuggestionOutcome.id, SuggestionOutcome.created_at < now - p.suggestion_outcomes
        )
        comments = await self._clear_old_comments(now - p.feedback_comments)
        return RunOutcome(
            stats={
                "events_deleted": events,
                "sync_runs_deleted": runs,
                "feedback_deleted": feedback,
                "comments_cleared": comments,
                "suggestion_outcomes_deleted": outcomes,
            }
        )

    async def _batched_delete(
        self, key_column: InstrumentedAttribute[Any], condition: ColumnElement[bool]
    ) -> int:
        table = key_column.class_
        total = 0
        while True:
            async with self._sessions.begin() as session:
                batch = select(key_column).where(condition).limit(BATCH_SIZE).scalar_subquery()
                result = cast(
                    CursorResult[Any],
                    await session.execute(delete(table).where(key_column.in_(batch))),
                )
            deleted = result.rowcount or 0
            total += deleted
            if deleted < BATCH_SIZE:
                return total

    async def _clear_old_comments(self, cutoff: datetime) -> int:
        total = 0
        while True:
            async with self._sessions.begin() as session:
                batch = (
                    select(RouteFeedback.feedback_id)
                    .where(RouteFeedback.created_at < cutoff, RouteFeedback.comment.is_not(None))
                    .limit(BATCH_SIZE)
                    .scalar_subquery()
                )
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(RouteFeedback)
                        .where(RouteFeedback.feedback_id.in_(batch))
                        .values(comment=None)
                    ),
                )
            updated = result.rowcount or 0
            total += updated
            if updated < BATCH_SIZE:
                return total
