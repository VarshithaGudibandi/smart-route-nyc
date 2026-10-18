import pytest

from app.services.feedback import clean_comment


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        (None, None),
        ("   ", None),
        ("Great route,\x00 thanks", "Great route, thanks"),
        ("Email me at rider.one+nyc@example.com", "Email me at [email removed]"),
        ("Call (212) 555-0147 if wrong", "Call [number removed] if wrong"),
        ("+1 917 555 0199", "[number removed]"),
        # Times, line names and short numbers are not phone numbers.
        ("The 2 at 08:34 took 45 min, 3 stops late", "The 2 at 08:34 took 45 min, 3 stops late"),
        ("Line one\nline two", "Line one\nline two"),
    ],
)
def test_clean_comment(raw: str | None, cleaned: str | None) -> None:
    assert clean_comment(raw) == cleaned
