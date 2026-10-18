from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.ai.preferences import InvalidModelOutputError
from app.ai.provider import LlmUnavailableError
from app.api.deps import Resources, get_resources, rate_limit
from app.core.errors import AiInvalidResponseError, AiUnavailableError
from app.schemas.errors import ErrorResponse
from app.schemas.preferences import InterpretRequest, InterpretResponse

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.post(
    "/interpret",
    dependencies=[Depends(rate_limit("interpret", lambda s: s.rate_limit_interpret_per_minute))],
    responses={
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def interpret_preferences(
    body: InterpretRequest, resources: Annotated[Resources, Depends(get_resources)]
) -> InterpretResponse:
    """Propose search settings from the rider's own words, for them to review.

    Only typed text uses the language model; the on-screen preset controls
    never call it. If the model is unavailable the rider keeps using those
    controls (503), and nothing they selected is ever removed by text.
    """
    try:
        return await resources.preferences.interpret(body.text, body.current)
    except LlmUnavailableError as exc:
        raise AiUnavailableError(str(exc), details={"reason": exc.code}) from exc
    except InvalidModelOutputError as exc:
        raise AiInvalidResponseError("The language model returned an unusable answer") from exc
