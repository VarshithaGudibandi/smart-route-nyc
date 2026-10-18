"""Transfer suggestions: proposals, and what verification made of them.

A proposal names one transfer: ride one of ``first_routes`` to
``transfer_station``, then one of ``second_routes`` onward. It is only a
hypothesis. A suggestion is shown only after OpenTripPlanner produced a
complete itinerary that follows it exactly and arrives earlier than the
baseline.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.routes import Notice, Route

MAX_PROPOSALS = 3

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


class TransferProposal(BaseModel):
    """Strict: also the schema a model must answer in (no coercion, no extras)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    first_routes: list[str] = Field(min_length=1, max_length=4)
    transfer_station: str = Field(max_length=64)
    second_routes: list[str] = Field(min_length=1, max_length=4)
    reason: str = Field(default="", max_length=200)

    def signature(self) -> tuple[frozenset[str], str, frozenset[str]]:
        return frozenset(self.first_routes), self.transfer_station, frozenset(self.second_routes)


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
