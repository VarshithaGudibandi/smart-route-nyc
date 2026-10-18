from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import Resources, get_resources, rate_limit
from app.core.errors import NotFoundError
from app.schemas.errors import ErrorResponse
from app.schemas.feedback import FeedbackRequest, FeedbackResponse

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("feedback", lambda s: s.rate_limit_feedback_per_minute))],
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
    },
)
async def submit_feedback(
    body: FeedbackRequest, resources: Annotated[Resources, Depends(get_resources)]
) -> FeedbackResponse:
    """Rate a route from a recent search, 1 to 5 stars, with an optional comment.

    No account is needed and nothing identifying is stored. Results can be
    rated for 15 minutes after the search.
    """
    if await resources.search_results.route(body.search_id, body.route_id) is None:
        raise NotFoundError("This route result is no longer available; search again to rate it")
    return await resources.feedback.record(body)
