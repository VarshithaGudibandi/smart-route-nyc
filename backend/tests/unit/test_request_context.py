import pytest

from app.core.request_context import resolve_request_id


def test_accepts_well_formed_incoming_id() -> None:
    assert resolve_request_id("abc-123_DEF.456") == "abc-123_DEF.456"


@pytest.mark.parametrize(
    "incoming",
    [
        None,
        "",
        "short",
        "x" * 65,
        "has space inside",
        "line\nbreak-injection",
        "<script>alert</script>",
    ],
)
def test_generates_id_for_missing_or_unsafe_values(incoming: str | None) -> None:
    generated = resolve_request_id(incoming)
    assert generated != incoming
    assert len(generated) == 32
