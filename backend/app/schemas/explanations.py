"""Route explanations grounded in facts the server computed."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.feedback import RouteId, SearchId


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: str = SearchId
    route_id: str = RouteId


class ExplanationFact(BaseModel):
    id: str
    kind: str
    text: str
    required: bool


class ExplanationSentence(BaseModel):
    text: str
    # The facts this sentence states; every sentence cites at least one.
    fact_ids: list[str]


class ModelSentence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=300)
    fact_ids: list[str] = Field(min_length=1, max_length=6)


class ModelExplanation(BaseModel):
    """The schema a model must answer in."""

    model_config = ConfigDict(extra="forbid", strict=True)

    # Enough for one sentence per fact on a long trip with many findings.
    sentences: list[ModelSentence] = Field(min_length=1, max_length=30)


class ExplanationResponse(BaseModel):
    search_id: str
    route_id: str
    # "facts": the fact texts themselves. "model": a model's wording that
    # passed every grounding check.
    source: Literal["facts", "model"]
    provider: str | None = None
    prompt_version: str | None = None
    cached: bool = False
    sentences: list[ExplanationSentence]
    facts: list[ExplanationFact]
    # Why a model's wording is not shown, when one was asked for.
    notice: str | None = None
