"""Helpers for model replies before they are validated."""


def strip_fence(text: str) -> str:
    """Accept JSON wrapped in a Markdown code fence; the JSON is validated afterwards."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = stripped[3:-3]
        stripped = stripped.removeprefix("json")
    return stripped.strip()
