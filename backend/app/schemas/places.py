"""Place search results: stations from the routing graph and places from OpenStreetMap."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.routes import Notice


class PlaceResult(BaseModel):
    id: str
    name: str
    # A short second line: the lines at a station, or the area of a place.
    detail: str | None = None
    kind: Literal["station", "place"]
    lat: float
    lon: float
    source: Literal["transit", "openstreetmap"]


class PlacesResponse(BaseModel):
    results: list[PlaceResult]
    notices: list[Notice]
    # Shown wherever OpenStreetMap results are displayed (ODbL requirement).
    attribution: str | None = None
