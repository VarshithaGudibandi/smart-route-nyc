"""Application factory and lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.baseline import KeywordBaselineProvider
from app.ai.guard import DailyCallBudget, GuardedLlm
from app.ai.preferences import PreferenceInterpreter
from app.ai.provider import DisabledProvider, LlmProvider
from app.api.deps import Resources
from app.api.v1.router import api_router
from app.cache.redis import ResilientRedis
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.graph_version import GraphVersion
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware, RequestSizeLimitMiddleware
from app.core.rate_limit import RateLimiter
from app.core.resilience import CircuitBreaker
from app.db.session import create_engine
from app.integrations.otp.client import OtpClient
from app.services.accessibility.snapshot import SnapshotProvider
from app.services.route_search import RouteSearchService
from app.services.suggestions.jobs import InvestigationRunner
from app.services.suggestions.outcomes import OutcomeRecorder
from app.services.suggestions.service import SuggestionService
from app.services.transit.alerts import AlertsProvider
from app.services.transit.knowledge import TransitKnowledge

logger = structlog.get_logger(__name__)


def build_provider(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "baseline":
        return KeywordBaselineProvider()
    return DisabledProvider()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis = ResilientRedis.from_url(
            str(settings.redis_url),
            timeout_ms=settings.redis_timeout_ms,
            failure_threshold=settings.redis_breaker_failure_threshold,
            reset_timeout_s=settings.redis_breaker_reset_s,
        )
        otp = OtpClient.create(
            settings.otp_base_url,
            timeout_s=settings.otp_timeout_s,
            retries=settings.otp_retries,
            failure_threshold=settings.otp_breaker_failure_threshold,
            reset_timeout_s=settings.otp_breaker_reset_s,
        )
        secret = settings.key_hashing_secret.get_secret_value().encode()
        db_engine = create_engine(settings)
        llm = GuardedLlm(
            build_provider(settings),
            DailyCallBudget(redis, settings.llm_daily_call_limit),
            CircuitBreaker(
                "llm", settings.llm_breaker_failure_threshold, settings.llm_breaker_reset_s
            ),
            timeout_s=settings.llm_timeout_s,
        )
        graph_version = GraphVersion(settings.otp_graph_version_file)
        accessibility = SnapshotProvider(db_engine, redis)
        investigations = None
        if settings.suggestions_enabled:
            # No language model generates proposals yet: only "baseline" or
            # "disabled" providers exist, and neither is a model.
            suggestions = SuggestionService(
                otp,
                TransitKnowledge(otp, redis, graph_version),
                AlertsProvider(db_engine, redis),
                accessibility,
            )
            investigations = InvestigationRunner(
                suggestions.investigate,
                redis,
                secret,
                max_concurrent=settings.investigation_max_concurrent,
                max_pending=settings.investigation_max_pending,
                timeout_s=settings.investigation_timeout_s,
                ttl_s=settings.investigation_ttl_s,
                poll_after_ms=settings.investigation_poll_after_ms,
                record_outcome=OutcomeRecorder(db_engine),
            )
        resources = Resources(
            settings=settings,
            db_engine=db_engine,
            redis=redis,
            otp=otp,
            rate_limiter=RateLimiter(redis, secret),
            route_search=RouteSearchService(otp, redis, graph_version, accessibility, settings),
            preferences=PreferenceInterpreter(llm, redis, secret, settings.preference_cache_ttl_s),
            investigations=investigations,
        )
        app.state.resources = resources
        logger.info("startup", env=settings.app_env)
        try:
            yield
        finally:
            if resources.investigations is not None:
                await resources.investigations.close()
            await resources.otp.close()
            await resources.redis.close()
            await resources.db_engine.dispose()
            logger.info("shutdown")

    app = FastAPI(
        title="SmartRouteNYC API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.app_env != "production" else None,
        openapi_url="/api/openapi.json" if settings.app_env != "production" else None,
    )
    register_error_handlers(app)

    # Starlette runs the last-added middleware first, so the request context
    # wraps everything, including CORS and size-limit rejections.
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=[
            "X-Request-ID",
            "X-Cache",
            "Retry-After",
            "RateLimit-Limit",
            "RateLimit-Remaining",
        ],
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(api_router)
    return app
