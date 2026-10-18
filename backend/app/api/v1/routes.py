from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Response, status

from app.api.deps import Resources, get_resources, rate_limit
from app.core.errors import NotFoundError
from app.schemas.errors import ErrorResponse
from app.schemas.routes import RouteSearchRequest, RouteSearchResponse
from app.schemas.suggestions import InvestigationView
from app.services.suggestions.jobs import InvestigationRunner

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
    result = outcome.response
    if resources.investigations is not None:
        baseline = result.routes or result.review_only_routes
        if baseline:
            result.investigation = await resources.investigations.start(
                outcome.cache_key, body, outcome.requested, baseline, result.graph_version
            )
    await resources.search_results.save(result)
    return result


InvestigationId = Annotated[str, Path(pattern=r"^[0-9a-f]{24}$")]
NOT_FOUND: dict[int | str, dict[str, Any]] = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}}


@router.get(
    "/investigations/{investigation_id}",
    dependencies=[
        Depends(
            rate_limit("investigation_poll", lambda s: s.rate_limit_investigation_poll_per_minute)
        )
    ],
    responses=NOT_FOUND,
)
async def get_investigation(
    investigation_id: InvestigationId,
    resources: Annotated[Resources, Depends(get_resources)],
) -> InvestigationView:
    """Transfer suggestions for a search, once its investigation finishes.

    Every suggestion is a complete OpenTripPlanner itinerary that makes the
    proposed transfer and arrives at least a minute earlier than the fastest
    route of the search.
    """
    view = await _runner(resources).view(investigation_id)
    if view is None:
        raise NotFoundError("Unknown or expired investigation")
    return view


@router.delete(
    "/investigations/{investigation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
async def cancel_investigation(
    investigation_id: InvestigationId,
    resources: Annotated[Resources, Depends(get_resources)],
) -> None:
    """Stop an investigation the client no longer needs."""
    if not await _runner(resources).cancel(investigation_id):
        raise NotFoundError("Unknown or expired investigation")


def _runner(resources: Resources) -> InvestigationRunner:
    if resources.investigations is None:
        raise NotFoundError("Transfer suggestions are disabled")
    return resources.investigations
