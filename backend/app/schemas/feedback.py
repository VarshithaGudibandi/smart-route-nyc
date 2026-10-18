"""Anonymous route feedback."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SearchId = Field(pattern=r"^[0-9a-f]{32}$")
RouteId = Field(pattern=r"^[0-9a-f]{16}$")


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: str = SearchId
    route_id: str = RouteId
    stars: int = Field(ge=1, le=5, strict=True)
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackResponse(BaseModel):
    feedback_id: str
    # A second rating of the same route result replaces the first.
    status: Literal["recorded", "updated"]
