"""Place search: stations first, then addresses and places.

Stations come from the routing graph and always work while OTP is up.
Addresses come from the geocoder; when it fails, stations are still
returned with a notice instead of an error.
"""

import structlog

from app.core.errors import RoutingTimeoutError, RoutingUnavailableError
from app.schemas.places import PlaceResult, PlacesResponse
from app.schemas.routes import Notice
from app.services.places.photon import PhotonGeocoder, PlaceSearchUnavailableError
from app.services.places.stations import StationIndex

logger = structlog.get_logger(__name__)

OSM_ATTRIBUTION = "© OpenStreetMap contributors"
MAX_STATIONS = 4


class PlaceSearch:
    def __init__(self, stations: StationIndex, geocoder: PhotonGeocoder | None) -> None:
        self._stations = stations
        self._geocoder = geocoder

    async def search(self, query: str, limit: int) -> PlacesResponse:
        notices: list[Notice] = []
        stations: list[PlaceResult] = []
        try:
            stations = await self._stations.search(query, min(MAX_STATIONS, limit))
        except (RoutingUnavailableError, RoutingTimeoutError):
            notices.append(
                Notice(code="STATIONS_UNAVAILABLE", message="Station search is unavailable.")
            )

        places: list[PlaceResult] = []
        if self._geocoder is not None and len(stations) < limit:
            try:
                places = await self._geocoder.search(query, limit - len(stations))
            except PlaceSearchUnavailableError:
                notices.append(
                    Notice(
                        code="ADDRESS_SEARCH_UNAVAILABLE",
                        message="Address search is unavailable right now; stations still work.",
                    )
                )
        return PlacesResponse(
            results=(stations + places)[:limit],
            notices=notices,
            attribution=OSM_ATTRIBUTION if places else None,
        )
