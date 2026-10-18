"""Models for turning a rider's own words into search preferences."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.routes import AccessibilityRequirements, OptimizationPreferences, StrictModel

PreferenceField = Literal[
    "step_free_required",
    "wheelchair_required",
    "max_walk_meters",
    "primary",
    "prefer_fewer_transfers",
    "prefer_less_walking",
]


class PreferenceState(StrictModel):
    accessibility: AccessibilityRequirements = Field(default_factory=AccessibilityRequirements)
    optimization: OptimizationPreferences = Field(default_factory=OptimizationPreferences)


class InterpretRequest(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    # What the rider has already selected with the on-screen controls.
    current: PreferenceState = Field(default_factory=PreferenceState)


class ModelOutput(BaseModel):
    """The only shape a model's answer may take. Null means "not stated".

    Strict: unknown fields are rejected and values are never coerced (the
    string "yes" is not a boolean), so a loosely-formed reply cannot slip
    through as a valid preference.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    step_free_required: bool | None = None
    wheelchair_required: bool | None = None
    max_walk_meters: int | None = Field(default=None, ge=100, le=5000)
    primary: Literal["FASTEST", "FEWEST_TRANSFERS", "LEAST_WALKING"] | None = None
    prefer_fewer_transfers: bool | None = None
    prefer_less_walking: bool | None = None
    uncertain_fields: list[PreferenceField] = Field(default_factory=list)
    unsupported_requests: list[Annotated[str, StringConstraints(max_length=120)]] = Field(
        default_factory=list, max_length=5
    )


class FieldChange(BaseModel):
    field: PreferenceField
    previous: Any
    proposed: Any


class Conflict(BaseModel):
    field: PreferenceField
    current: Any
    interpreted: Any
    resolution: Literal["kept_current"]
    message: str


class InterpretationSource(BaseModel):
    provider: str
    model: str
    prompt_version: str
    cached: bool


class InterpretResponse(BaseModel):
    """A proposal for the rider to review; nothing is applied until they confirm."""

    proposed: PreferenceState
    changes: list[FieldChange]
    conflicts: list[Conflict]
    uncertain_fields: list[PreferenceField]
    unsupported_requests: list[str]
    source: InterpretationSource
