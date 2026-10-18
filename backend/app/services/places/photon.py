"""Addresses and places from Photon, an open-source OpenStreetMap geocoder.

Called from the backend, never from browsers, so its public server sees
one polite client: results are limited to the service area, cached in
Redis by an HMAC of the query, and calls stop for a while after repeated
failures (circuit breaker). Photon publishes no usage limits; heavy use
should move to a self-hosted instance, which only needs ``PHOTON_BASE_URL``.
"""

import hashlib
import hmac
import json
from typing import Any

import httpx
import structlog

from app.cache.redis import ResilientRedis
from app.core.resilience import CircuitBreaker, CircuitOpenError
from app.schemas.places import PlaceResult
from app.schemas.routes import (
    SERVICE_AREA_EAST,
    SERVICE_AREA_NORTH,
    SERVICE_AREA_SOUTH,
    SERVICE_AREA_WEST,
)

logger = structlog.get_logger(__name__)

BBOX = f"{SERVICE_AREA_WEST},{SERVICE_AREA_SOUTH},{SERVICE_AREA_EAST},{SERVICE_AREA_NORTH}"
CACHE_VERSION = 1


class PlaceSearchUnavailableError(Exception):
    """The geocoder could not answer; station search still works."""


def parse_features(payload: dict[str, Any]) -> list[PlaceResult]:
    results = []
    for feature in payload.get("features", []):
        props = feature.get("properties") or {}
        lon, lat = feature["geometry"]["coordinates"]
        if not (SERVICE_AREA_SOUTH <= lat <= SERVICE_AREA_NORTH):
            continue
        if not (SERVICE_AREA_WEST <= lon <= SERVICE_AREA_EAST):
            continue
        street = " ".join(p for p in (props.get("housenumber"), props.get("street")) if p)
        name = props.get("name") or street
        if not name:
            continue
        area = [
            part
            for part in (
                street if street != name else None,
                props.get("district") or props.get("locality"),
                props.get("city"),
                props.get("state"),
            )
            if part
        ]
        results.append(
            PlaceResult(
                id=f"osm:{props.get('osm_type', '')}{props.get('osm_id', '')}",
                name=name,
                detail=", ".join(dict.fromkeys(area)) or None,
                kind="place",
                lat=lat,
                lon=lon,
                source="openstreetmap",
            )
        )
    return results


class PhotonGeocoder:
    def __init__(
        self,
        http: httpx.AsyncClient,
        breaker: CircuitBreaker,
        redis: ResilientRedis,
        secret: bytes,
        cache_ttl_s: int,
    ) -> None:
        self._http = http
        self._breaker = breaker
        self._redis = redis
        self._secret = secret
        self._cache_ttl_s = cache_ttl_s

    async def search(self, query: str, limit: int) -> list[PlaceResult]:
        key = self._key(query, limit)
        if (cached := await self._redis.get(key)) is not None:
            return [PlaceResult.model_validate(r) for r in json.loads(cached)]
        try:
            self._breaker.before_call()
        except CircuitOpenError as exc:
            raise PlaceSearchUnavailableError("geocoder circuit open") from exc
        try:
            response = await self._http.get(
                "/api/", params={"q": query, "limit": limit, "bbox": BBOX, "lang": "en"}
            )
            response.raise_for_status()
            results = parse_features(response.json())
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            self._breaker.record_failure()
            logger.warning("geocoder_failed", error=type(exc).__name__)
            raise PlaceSearchUnavailableError("geocoder failed") from exc
        self._breaker.record_success()
        payload = json.dumps([r.model_dump() for r in results]).encode()
        await self._redis.set(key, payload, ttl_s=self._cache_ttl_s)
        return results

    def _key(self, query: str, limit: int) -> str:
        material = f"{CACHE_VERSION}|{limit}|{' '.join(query.lower().split())}"
        digest = hmac.new(self._secret, material.encode(), hashlib.sha256).hexdigest()[:32]
        return f"srn:v1:geocode:{digest}"
