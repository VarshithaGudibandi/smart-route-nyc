"""Application factory and lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.anthropic_provider import AnthropicProvider
from app.ai.baseline import KeywordBaselineProvider
from app.ai.guard import DailyCallBudget, GuardedLlm
from app.ai.openai_compatible import OpenAICompatibleProvider
from app.ai.preferences import PreferenceInterpreter
from app.ai.provider import DisabledProvider, LlmProvider
from app.ai.suggestions import ModelProposalGenerator
from app.api.deps import Resources
from app.api.v1.router import api_router
from app.cache.redis import ResilientRedis
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.graph_version import GraphVersion
from app.core.logging import configure_logging
from app.core.metrics import serve_metrics
from app.core.middleware import (
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.rate_limit import RateLimiter
from app.core.resilience import CircuitBreaker
from app.db.session import create_engine
from app.integrations.otp.client import OtpClient
from app.services.accessibility.snapshot import SnapshotProvider
from app.services.explanations.service import ExplanationService
from app.services.feedback import FeedbackStore
from app.services.places.photon import PhotonGeocoder
from app.services.places.service import PlaceSearch
from app.services.places.stations import StationIndex
from app.services.route_search import RouteSearchService
from app.services.search_results import SearchResults
from app.services.suggestions.jobs import InvestigationRunner
from app.services.suggestions.outcomes import OutcomeRecorder
from app.services.suggestions.service import SuggestionService
from app.services.transit.alerts import AlertsProvider
from app.services.transit.knowledge import TransitKnowledge
from app.services.transit.same_platform import SamePlatformChanges

USER_AGENT = "SmartRouteNYC/0.1 (+https://github.com/VarshithaGudibandi/smart-route-nyc)"
logger = structlog.get_logger(__name__)


def build_provider(settings: Settings) -> LlmProvider:
    provider = settings.resolved_llm_provider
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    if provider == "anthropic":
        return AnthropicProvider.create(key, settings.resolved_llm_model, settings.llm_effort)
    if provider == "openai":
        return OpenAICompatibleProvider.create(
            settings.llm_base_url, key, settings.resolved_llm_model
        )
    if provider == "baseline":
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
        same_platform = SamePlatformChanges(settings.same_platform_file)
        accessibility = SnapshotProvider(db_engine, redis)
        alerts = AlertsProvider(db_engine, redis)
        # Only a real model writes explanations and proposes transfers; the
        # keyword baseline handles typed preferences alone.
        model_llm = llm if settings.uses_model else None
        investigations = None
        if settings.suggestions_enabled:
            suggestions = SuggestionService(
                otp,
                TransitKnowledge(otp, redis, graph_version),
                alerts,
                accessibility,
                ModelProposalGenerator(model_llm) if model_llm else None,
                same_platform,
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
        geocoder = None
        geocoder_http = None
        if settings.photon_base_url:
            geocoder_http = httpx.AsyncClient(
                base_url=settings.photon_base_url,
                timeout=httpx.Timeout(settings.photon_timeout_s),
                headers={"User-Agent": USER_AGENT},
            )
            geocoder = PhotonGeocoder(
                geocoder_http,
                CircuitBreaker("geocoder", 3, 60),
                redis,
                secret,
                settings.place_cache_ttl_s,
            )
        search_results = SearchResults(
            redis,
            settings.search_result_ttl_s,
            investigations.suggestion_routes if investigations else None,
        )
        resources = Resources(
            settings=settings,
            db_engine=db_engine,
            redis=redis,
            otp=otp,
            rate_limiter=RateLimiter(redis, secret),
            route_search=RouteSearchService(
                otp, redis, graph_version, accessibility, settings, same_platform
            ),
            preferences=PreferenceInterpreter(llm, redis, secret, settings.preference_cache_ttl_s),
            investigations=investigations,
            search_results=search_results,
            feedback=FeedbackStore(db_engine, secret),
            explanations=ExplanationService(
                search_results,
                accessibility,
                alerts,
                model_llm,
                redis,
                secret,
                settings.explanation_cache_ttl_s,
            ),
            places=PlaceSearch(StationIndex(otp, graph_version), geocoder),
        )
        app.state.resources = resources
        metrics = serve_metrics(settings.metrics_port)
        logger.info("startup", env=settings.app_env)
        try:
            yield
        finally:
            if metrics is not None:
                metrics.shutdown()
            if resources.investigations is not None:
                await resources.investigations.close()
            await resources.otp.close()
            if geocoder_http is not None:
                await geocoder_http.aclose()
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
    # Outside CORS, so preflight and error responses carry the headers too.
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.app_env == "production")
    app.add_middleware(RequestContextMiddleware)

    app.include_router(api_router)
    return app
