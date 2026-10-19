"""Store what became of each proposal, so suggestion quality can be measured
in production the same way the evaluation measures it offline."""

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models import SuggestionOutcome
from app.schemas.suggestions import InvestigationResult, TransferProposal

logger = structlog.get_logger(__name__)


class OutcomeRecorder:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def __call__(self, graph_version: str, result: InvestigationResult) -> None:
        rows = [
            SuggestionOutcome(
                graph_version=graph_version,
                generator=result.generator,
                source=s.source,
                first_routes=",".join(s.proposal.first_routes),
                transfer_station=s.proposal.transfer_station,
                second_routes=",".join(s.proposal.second_routes),
                later_transfers=_later(s.proposal),
                outcome="verified",
                saves_seconds=s.saves_seconds,
            )
            for s in result.suggestions
        ] + [
            SuggestionOutcome(
                graph_version=graph_version,
                generator=result.generator,
                source=r.source,
                first_routes=",".join(r.proposal.first_routes)[:128],
                transfer_station=r.proposal.transfer_station,
                second_routes=",".join(r.proposal.second_routes)[:128],
                later_transfers=_later(r.proposal),
                outcome=r.code,
            )
            for r in result.rejected
        ]
        if not rows:
            return
        try:
            async with AsyncSession(self._engine) as session, session.begin():
                session.add_all(rows)
        except SQLAlchemyError as exc:
            # Measurement only: losing it never affects what the rider sees.
            logger.warning("suggestion_outcomes_not_stored", error=type(exc).__name__)


def _later(proposal: TransferProposal) -> str | None:
    text = ";".join(f"{t.station}>{','.join(t.routes)}" for t in proposal.then)
    return text[:256] or None
