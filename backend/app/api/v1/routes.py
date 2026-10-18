from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import Resources, get_resources, rate_limit
from app.schemas.errors import ErrorResponse
from app.schemas.routes import RouteSearchRequest, RouteSearchResponse

router = APIRouter(prefix="/routes", tags=["routes"])


@router.post(
    "/search",
    dependencies=[
        Depends(rate_limit("route_search", lambda s: s.rate_limit_route_search_per_minute))
    ],
    responses={
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def search_routes(
    body: RouteSearchRequest,
    response: Response,
    resources: Annotated[Resources, Depends(get_resources)],
) -> RouteSearchResponse:
    """Plan journeys between two points using OpenTripPlanner.

    Returns up to five itineraries. An empty ``routes`` list with ``notices``
    is a valid answer (for example, no service at that time), not an error.
    """
    outcome = await resources.route_search.search(body)
    response.headers["X-Cache"] = outcome.cache.upper()
    return outcome.response
