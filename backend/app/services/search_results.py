"""Keep each search's results briefly, so later requests can refer to them.

Explanations and feedback name a route by ``search_id`` and ``route_id``
instead of sending the route itself: a client could otherwise ask for an
explanation of a route it made up, or rate a search that never happened.
The ``search_id`` is a random 128-bit value issued with the response, so it
also works as an unguessable reference.

Suggestion routes are found through the search's investigation, which holds
them for as long as it exists.
"""

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from app.cache.redis import ResilientRedis
from app.schemas.routes import DataFreshness, Route, RouteSearchResponse

KEY_PREFIX = "srn:v1:search"

# Suggestion routes of an investigation, by investigation ID.
SuggestionRoutes = Callable[[str], Awaitable[list[Route]]]


class StoredSearch(BaseModel):
    search_id: str
    graph_version: str
    routes: list[Route]
    data_freshness: list[DataFreshness]
    investigation_id: str | None = None


class SearchResults:
    def __init__(
        self, redis: ResilientRedis, ttl_s: int, suggestions: SuggestionRoutes | None = None
    ) -> None:
        self._redis = redis
        self._ttl_s = ttl_s
        self._suggestions = suggestions

    async def save(self, response: RouteSearchResponse) -> None:
        stored = StoredSearch(
            search_id=response.search_id,
            graph_version=response.graph_version,
            routes=response.routes + response.review_only_routes,
            data_freshness=response.data_freshness,
            investigation_id=response.investigation.id if response.investigation else None,
        )
        await self._redis.set(
            self._key(response.search_id), stored.model_dump_json().encode(), ttl_s=self._ttl_s
        )

    async def get(self, search_id: str) -> StoredSearch | None:
        raw = await self._redis.get(self._key(search_id))
        if raw is None:
            return None
        try:
            return StoredSearch.model_validate_json(raw)
        except ValidationError:
            return None

    def _key(self, search_id: str) -> str:
        return f"{KEY_PREFIX}:{search_id}"

    async def route(self, search_id: str, route_id: str) -> tuple[StoredSearch, Route] | None:
        """A route of the search, or of its verified suggestions."""
        stored = await self.get(search_id)
        if stored is None:
            return None
        candidates = list(stored.routes)
        if stored.investigation_id and self._suggestions is not None:
            candidates += await self._suggestions(stored.investigation_id)
        route = next((r for r in candidates if r.route_id == route_id), None)
        return (stored, route) if route is not None else None
