"""Transfer suggestions: proposals, and what verification made of them.

A proposal is a chain of rides through the subway part of a journey: ride
one of ``first_routes`` to ``transfer_station``, then one of
``second_routes``, and, for each entry in ``then``, ride on to its station
and change to its routes. Local, express, back to local is
``first_routes=[1], transfer_station=Chambers St, second_routes=[2, 3],
then=[{station: 96 St, routes: [1]}]``.

It is only a hypothesis. A suggestion is shown only after OpenTripPlanner
produced a complete itinerary that follows it exactly and arrives earlier
than the baseline.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.routes import InvestigationState, Notice, Route

MAX_PROPOSALS = 3
# Changes a proposal may add after the first: up to three transfers in all.
MAX_LATER_TRANSFERS = 2

ProposalSource = Literal["rules", "model"]
RejectionCode = Literal[
    "unknown_id",
    "not_served",
    "duplicate",
    "same_as_baseline",
    "line_suspended",
    "no_itinerary",
    "not_faster",
    "accessibility",
]


class LaterTransfer(BaseModel):
    """Ride on to ``station`` and change there to one of ``routes``."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    station: str = Field(max_length=64)
    routes: list[str] = Field(min_length=1, max_length=4)


class TransferProposal(BaseModel):
    """Strict: also the schema a model must answer in (no coercion, no extras)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    first_routes: list[str] = Field(min_length=1, max_length=4)
    transfer_station: str = Field(max_length=64)
    second_routes: list[str] = Field(min_length=1, max_length=4)
    then: list[LaterTransfer] = Field(default_factory=list, max_length=MAX_LATER_TRANSFERS)
    reason: str = Field(default="", max_length=200)

    @property
    def stations(self) -> list[str]:
        """Where the rider changes, in order."""
        return [self.transfer_station, *(t.station for t in self.then)]

    @property
    def rides(self) -> list[list[str]]:
        """The lines allowed for each ride, in order."""
        return [self.first_routes, self.second_routes, *(t.routes for t in self.then)]

    def signature(self) -> tuple[tuple[frozenset[str], ...], tuple[str, ...]]:
        return tuple(frozenset(r) for r in self.rides), tuple(self.stations)


class ModelProposals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    proposals: list[TransferProposal] = Field(max_length=MAX_PROPOSALS)


class RejectedProposal(BaseModel):
    proposal: TransferProposal
    source: ProposalSource
    code: RejectionCode
    detail: str


class VerifiedSuggestion(BaseModel):
    proposal: TransferProposal
    source: ProposalSource
    route: Route
    # Compared with the fastest baseline route that meets the same requirements.
    saves_seconds: int
    notices: list[Notice] = Field(default_factory=list)


class InvestigationResult(BaseModel):
    """Everything an investigation found, including why proposals failed."""

    applicable: bool
    reason: str | None = None
    generator: str
    suggestions: list[VerifiedSuggestion] = Field(default_factory=list)
    rejected: list[RejectedProposal] = Field(default_factory=list)


TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


class InvestigationView(BaseModel):
    id: str
    status: InvestigationState
    generator: str | None = None
    applicable: bool | None = None
    reason: str | None = None
    suggestions: list[VerifiedSuggestion] = Field(default_factory=list)
    # How many proposals were rejected, by reason.
    rejected: dict[str, int] = Field(default_factory=dict)
    updated_at: datetime
