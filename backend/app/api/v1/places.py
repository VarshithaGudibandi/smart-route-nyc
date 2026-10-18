from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Resources, get_resources, rate_limit
from app.schemas.errors import ErrorResponse
from app.schemas.places import PlacesResponse

router = APIRouter(prefix="/places", tags=["places"])


@router.get(
    "",
    dependencies=[Depends(rate_limit("places", lambda s: s.rate_limit_places_per_minute))],
    responses={status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse}},
)
async def search_places(
    resources: Annotated[Resources, Depends(get_resources)],
    q: Annotated[str, Query(min_length=2, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=10)] = 6,
) -> PlacesResponse:
    """Stations, then addresses and places, matching ``q`` within the service area.

    Station results come from the routing graph. Address results come from
    OpenStreetMap through Photon; when it is unavailable, stations are still
    returned with a notice.
    """
    return await resources.places.search(q.strip(), limit)
