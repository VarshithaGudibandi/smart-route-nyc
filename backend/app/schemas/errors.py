from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Body returned for every non-2xx response."""

    code: str
    message: str
    request_id: str | None
    details: Any | None = None
