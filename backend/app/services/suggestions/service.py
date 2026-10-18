"""Investigate whether a different transfer beats the baseline routes.

1. Take the fastest baseline route and its subway segment.
2. Build timetable facts for the transfers that segment could make.
3. Collect proposals: from the model when one is configured, and from the
   rules generator. A model that fails or answers invalidly costs nothing
   but its own proposals.
4. Check every proposal against the facts, drop duplicates, and verify at
   most ``MAX_VERIFICATIONS`` with OTP, a few at a time.
5. Return verified suggestions, fastest first, with every rejection and its
   reason, so the outcome can be measured.
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

import structlog

from app.ai.provider import LlmUnavailableError
from app.ai.suggestions import InvalidProposalsError
from app.core.errors import AppError
from app.integrations.otp.client import OtpClient
from app.schemas.routes import Route, RouteSearchRequest
from app.schemas.suggestions import (
    InvestigationResult,
    ProposalSource,
    RejectedProposal,
    TransferProposal,
    VerifiedSuggestion,
)
from app.services.accessibility.snapshot import SnapshotProvider
from app.services.suggestions import rules
from app.services.suggestions.context import (
    StationLookup,
    TransferFacts,
    build_facts,
    subway_segment,
)
from app.services.suggestions.verifier import VerificationInput, verify
from app.services.transit.alerts import AlertSnapshot

logger = structlog.get_logger(__name__)

MAX_VERIFICATIONS = 4
CONCURRENT_VERIFICATIONS = 2


class AlertSource(Protocol):
    async def current(self) -> AlertSnapshot: ...


class ProposalGenerator(Protocol):
    @property
    def name(self) -> str: ...

    async def propose(self, facts: TransferFacts) -> list[TransferProposal]: ...


class SuggestionService:
    def __init__(
        self,
        otp: OtpClient,
        knowledge: StationLookup,
        alerts: AlertSource,
        accessibility: SnapshotProvider,
        model: ProposalGenerator | None = None,
    ) -> None:
        self._otp = otp
        self._knowledge = knowledge
        self._alerts = alerts
        self._accessibility = accessibility
        self._model = model

    @property
    def generator(self) -> str:
        return f"{self._model.name}+rules" if self._model else "rules"

    async def investigate(
        self, request: RouteSearchRequest, baseline: Sequence[Route]
    ) -> InvestigationResult:
        def not_applicable(reason: str) -> InvestigationResult:
            return InvestigationResult(applicable=False, reason=reason, generator=self.generator)

        if not baseline:
            return not_applicable("There is no baseline route to improve on.")
        fastest = min(baseline, key=lambda r: r.arrival)
        segment = subway_segment(fastest)
        if segment is None:
            return not_applicable("The fastest route has no single subway segment.")

        alerts = await self._alerts.current()
        facts = await build_facts(segment, self._knowledge, alerts)
        if facts is None or not facts.options:
            return not_applicable("No transfer stations were found on this subway segment.")

        proposals: list[tuple[TransferProposal, ProposalSource]] = []
        if self._model is not None:
            try:
                proposals += [(p, "model") for p in await self._model.propose(facts)]
            except (LlmUnavailableError, InvalidProposalsError, AppError) as exc:
                logger.info("model_proposals_unavailable", error=type(exc).__name__)
        proposals += [(p, "rules") for p in rules.propose(facts)]

        rejected: list[RejectedProposal] = []
        to_verify: list[tuple[TransferProposal, ProposalSource]] = []
        seen: set[tuple[frozenset[str], str, frozenset[str]]] = set()
        for proposal, source in proposals:
            if proposal.signature() in seen:
                continue
            seen.add(proposal.signature())
            if problem := rules.check(proposal, facts):
                code, detail = problem
                rejected.append(
                    RejectedProposal(proposal=proposal, source=source, code=code, detail=detail)
                )
            elif len(to_verify) < MAX_VERIFICATIONS:
                to_verify.append((proposal, source))

        now = datetime.now(UTC)
        data = VerificationInput(
            request=request,
            requested=request.depart_at or now,
            segment=segment,
            baseline_arrival=fastest.arrival,
            accessibility=await self._accessibility.current(),
            alerts=alerts,
            now=now,
        )
        limit = asyncio.Semaphore(CONCURRENT_VERIFICATIONS)

        async def bounded(
            proposal: TransferProposal, source: ProposalSource
        ) -> VerifiedSuggestion | RejectedProposal:
            async with limit:
                return await verify(self._otp, proposal, source, data)

        outcomes = await asyncio.gather(*(bounded(p, s) for p, s in to_verify))
        suggestions = [o for o in outcomes if isinstance(o, VerifiedSuggestion)]
        rejected += [o for o in outcomes if isinstance(o, RejectedProposal)]
        suggestions.sort(key=lambda s: s.route.arrival)
        logger.info(
            "suggestions_investigated",
            generator=self.generator,
            proposals=len(proposals),
            verified=len(suggestions),
            rejected=[r.code for r in rejected],
        )
        return InvestigationResult(
            applicable=True,
            generator=self.generator,
            suggestions=suggestions,
            rejected=rejected,
        )
