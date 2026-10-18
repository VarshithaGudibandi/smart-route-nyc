import pytest

from app.ai.preferences import InvalidModelOutputError, clean_text, merge, parse_model_output
from app.schemas.preferences import InterpretationSource, ModelOutput, PreferenceState

SOURCE = InterpretationSource(provider="test", model="m", prompt_version="v", cached=False)


def state(**accessibility: object) -> PreferenceState:
    return PreferenceState.model_validate({"accessibility": accessibility})


def test_text_can_add_requirements() -> None:
    result = merge(state(), ModelOutput(step_free_required=True), SOURCE)
    assert result.proposed.accessibility.step_free_required is True
    assert [(c.field, c.proposed) for c in result.changes] == [("step_free_required", True)]


def test_text_can_never_remove_a_selected_requirement() -> None:
    result = merge(
        state(wheelchair_required=True, step_free_required=True),
        ModelOutput(wheelchair_required=False, step_free_required=False),
        SOURCE,
    )
    assert result.proposed.accessibility.wheelchair_required is True
    assert result.proposed.accessibility.step_free_required is True
    assert {c.field for c in result.conflicts} == {"wheelchair_required", "step_free_required"}
    assert all(c.resolution == "kept_current" for c in result.conflicts)


def test_wheelchair_implies_step_free() -> None:
    result = merge(state(), ModelOutput(wheelchair_required=True), SOURCE)
    assert result.proposed.accessibility.step_free_required is True
    assert {c.field for c in result.changes} == {"wheelchair_required", "step_free_required"}


def test_uncertain_fields_are_not_applied() -> None:
    output = ModelOutput(step_free_required=True, uncertain_fields=["step_free_required"])
    result = merge(state(), output, SOURCE)
    assert result.proposed.accessibility.step_free_required is False
    assert result.uncertain_fields == ["step_free_required"]


def test_walking_limit_only_tightens() -> None:
    tighter = merge(state(max_walk_meters=800), ModelOutput(max_walk_meters=400), SOURCE)
    looser = merge(state(max_walk_meters=400), ModelOutput(max_walk_meters=2000), SOURCE)
    assert tighter.proposed.accessibility.max_walk_meters == 400
    assert looser.proposed.accessibility.max_walk_meters == 400
    assert [c.field for c in looser.conflicts] == ["max_walk_meters"]


def test_preferences_can_change() -> None:
    current = PreferenceState.model_validate({"optimization": {"primary": "LEAST_WALKING"}})
    result = merge(current, ModelOutput(primary="FEWEST_TRANSFERS"), SOURCE)
    assert result.proposed.optimization.primary == "FEWEST_TRANSFERS"


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        '{"step_free_required": "yes"}',
        '{"admin": true}',
        '{"primary": "TELEPORT"}',
        '{"max_walk_meters": 99999}',
        '{"uncertain_fields": ["password"]}',
        '{"unsupported_requests": ["a", "b", "c", "d", "e", "f"]}',
    ],
)
def test_rejects_anything_outside_the_schema(reply: str) -> None:
    with pytest.raises(InvalidModelOutputError):
        parse_model_output(reply)


def test_clean_text_strips_control_characters() -> None:
    assert clean_text("  no\x00 stairs\n\tplease  ") == "no stairs please"
