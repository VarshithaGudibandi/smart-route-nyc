"""FastAPI dependencies giving endpoints access to shared resources."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.preferences import PreferenceInterpreter
from app.cache.redis import ResilientRedis
from app.core.config import Settings
from app.core.errors import RateLimitedError
from app.core.rate_limit import RateLimiter
from app.integrations.otp.client import OtpClient
from app.services.explanations.service import ExplanationService
from app.services.feedback import FeedbackStore
from app.services.route_search import RouteSearchService
from app.services.search_results import SearchResults
from app.services.suggestions.jobs import InvestigationRunner


@dataclass(frozen=True)
class Resources:
    """Long-lived clients and services created at startup and closed at shutdown."""

    settings: Settings
    db_engine: AsyncEngine
    redis: ResilientRedis
    otp: OtpClient
    rate_limiter: RateLimiter
    route_search: RouteSearchService
    preferences: PreferenceInterpreter
    search_results: SearchResults
    feedback: FeedbackStore
    explanations: ExplanationService
    # None when suggestions are disabled.
    investigations: InvestigationRunner | None = None


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


def rate_limit(
    scope: str, limit: Callable[[Settings], int], window_s: int = 60
) -> Callable[..., Awaitable[None]]:
    """Dependency enforcing ``limit`` requests per client per window."""

    async def check(
        request: Request,
        response: Response,
        resources: Annotated[Resources, Depends(get_resources)],
    ) -> None:
        client = request.client.host if request.client else "unknown"
        decision = await resources.rate_limiter.hit(
            scope, client, limit(resources.settings), window_s
        )
        response.headers["RateLimit-Limit"] = str(decision.limit)
        response.headers["RateLimit-Remaining"] = str(decision.remaining)
        if not decision.allowed:
            raise RateLimitedError(
                "Too many requests; try again shortly",
                headers={"Retry-After": str(decision.retry_after_s)},
            )

    return check
