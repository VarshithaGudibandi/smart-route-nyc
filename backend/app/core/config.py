"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    max_request_body_bytes: int = Field(default=16 * 1024, gt=0)

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://smartroute:smartroute@localhost:5432/smartroute"
    )
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_s: float = Field(default=3.0, gt=0)
    db_statement_timeout_ms: int = Field(default=5000, gt=0)

    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    redis_timeout_ms: int = Field(default=100, gt=0)
    redis_breaker_failure_threshold: int = Field(default=3, ge=1)
    redis_breaker_reset_s: float = Field(default=10.0, gt=0)

    otp_base_url: str = "http://localhost:8080"
    otp_timeout_s: float = Field(default=6.0, gt=0)
    otp_retries: int = Field(default=1, ge=0, le=3)
    otp_breaker_failure_threshold: int = Field(default=5, ge=1)
    otp_breaker_reset_s: float = Field(default=15.0, gt=0)
    # Written by otp/scripts/build_graph.sh; changes whenever the graph is rebuilt.
    otp_graph_version_file: Path | None = None

    # Secret mixed into cache and rate-limit keys so they reveal nothing about
    # locations or client addresses. Must be overridden in production.
    key_hashing_secret: SecretStr = SecretStr("development-only-secret")

    search_itineraries: int = Field(default=5, ge=1, le=10)
    route_cache_ttl_s: int = Field(default=600, gt=0)
    # Departures within the hour. The graph carries no realtime updates, so a
    # result holds for its whole 5-minute departure bucket.
    route_cache_near_term_ttl_s: int = Field(default=300, gt=0)
    route_cache_empty_ttl_s: int = Field(default=30, gt=0)
    single_flight_lock_ttl_ms: int = Field(default=10_000, gt=0)
    single_flight_wait_s: float = Field(default=3.0, gt=0)

    rate_limit_route_search_per_minute: int = Field(default=20, ge=1)
    rate_limit_interpret_per_minute: int = Field(default=10, ge=1)
    rate_limit_investigation_poll_per_minute: int = Field(default=120, ge=1)
    rate_limit_feedback_per_minute: int = Field(default=5, ge=1)
    rate_limit_places_per_minute: int = Field(default=60, ge=1)
    rate_limit_explain_per_minute: int = Field(default=20, ge=1)

    # Address search through Photon (OpenStreetMap). Empty URL: stations only.
    photon_base_url: str = "https://photon.komoot.io"
    photon_timeout_s: float = Field(default=2.5, gt=0)
    place_cache_ttl_s: int = Field(default=86_400, gt=0)

    # How long a search's results can be explained or rated.
    search_result_ttl_s: int = Field(default=900, ge=60)
    explanation_cache_ttl_s: int = Field(default=3600, gt=0)

    # Transfer suggestions, investigated in the background after a search.
    suggestions_enabled: bool = True
    investigation_max_concurrent: int = Field(default=2, ge=1)
    investigation_max_pending: int = Field(default=16, ge=1)
    # Includes a model call when a language model is configured.
    investigation_timeout_s: float = Field(default=40.0, gt=0)
    investigation_ttl_s: int = Field(default=900, ge=60)
    investigation_poll_after_ms: int = Field(default=1500, ge=100)

    # Language model, from any provider. "openai" means any service with the
    # OpenAI-compatible chat API (OpenAI, Gemini, Groq, Mistral, OpenRouter,
    # Ollama) at LLM_BASE_URL; "anthropic" is Claude through Anthropic's SDK.
    # "auto" picks from the key: Anthropic keys start with "sk-ant-". Without a
    # key, "baseline" keyword rules answer typed preferences.
    llm_provider: Literal["auto", "anthropic", "openai", "baseline", "disabled"] = "auto"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    # Claude only: these are short, structured tasks; low effort keeps them quick.
    llm_effort: Literal["low", "medium", "high"] = "low"
    llm_timeout_s: float = Field(default=20.0, gt=0)
    llm_daily_call_limit: int = Field(default=2000, ge=1)
    llm_breaker_failure_threshold: int = Field(default=5, ge=1)
    llm_breaker_reset_s: float = Field(default=60.0, gt=0)
    preference_cache_ttl_s: int = Field(default=86_400, gt=0)

    # Ingestion worker sources (public feeds; no API keys required).
    mta_feed_base_url: str = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds"
    path_gtfs_rt_url: str = "https://path.transitdata.nyc/gtfsrt"
    mta_bus_gtfs_rt_url: str = "https://gtfsrt.prod.obanyc.com/vehiclePositions"
    # manifest.json written by otp/scripts/fetch_inputs.py next to the graph.
    gtfs_manifest_file: Path | None = None
    # Stations where the graph allows a 1-minute same-platform change.
    same_platform_file: Path | None = None

    ingest_elevators_interval_s: int = Field(default=120, ge=10)
    ingest_realtime_interval_s: int = Field(default=60, ge=10)
    ingest_alerts_interval_s: int = Field(default=120, ge=10)
    ingest_static_interval_s: int = Field(default=3600, ge=60)
    ingest_retention_interval_s: int = Field(default=86_400, ge=60)
    ingest_fetch_timeout_s: float = Field(default=20.0, gt=0)

    retention_events_days: int = Field(default=90, ge=1)
    retention_sync_runs_days: int = Field(default=30, ge=1)
    retention_feedback_comment_days: int = Field(default=365, ge=1)
    retention_feedback_days: int = Field(default=730, ge=1)
    retention_suggestion_outcomes_days: int = Field(default=180, ge=1)

    health_check_timeout_s: float = Field(default=1.0, gt=0)
    # Prometheus metrics on their own port, for the internal network only. Unset: off.
    metrics_port: int | None = Field(default=None, ge=1024, le=65535)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, as environment variables provide."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("llm_api_key", "llm_model", mode="before")
    @classmethod
    def empty_key_is_no_key(cls, value: object) -> object:
        """Compose passes an unset variable as an empty string."""
        return None if value == "" else value

    @model_validator(mode="after")
    def check_production_settings(self) -> Self:
        """Refuse to start in production with settings only fit for a laptop."""
        if self.app_env != "production":
            return self
        secret = self.key_hashing_secret.get_secret_value()
        if secret == "development-only-secret":
            raise ValueError("KEY_HASHING_SECRET must be set in production")
        if len(secret) < 32:
            raise ValueError("KEY_HASHING_SECRET must be at least 32 characters in production")
        for origin in self.allowed_origins:
            if origin == "*" or not origin.startswith("https://"):
                raise ValueError(f"ALLOWED_ORIGINS must list https origins in production: {origin}")
        if "smartroute:smartroute@" in str(self.database_url):
            raise ValueError("DATABASE_URL uses the local development password")
        return self

    @property
    def resolved_llm_provider(self) -> Literal["anthropic", "openai", "baseline", "disabled"]:
        if self.llm_provider != "auto":
            return self.llm_provider
        if self.llm_api_key is None:
            return "baseline"
        key = self.llm_api_key.get_secret_value()
        return "anthropic" if key.startswith("sk-ant-") else "openai"

    @property
    def uses_model(self) -> bool:
        return self.resolved_llm_provider in ("anthropic", "openai")

    @property
    def resolved_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return DEFAULT_CLAUDE_MODEL if self.resolved_llm_provider == "anthropic" else ""

    @model_validator(mode="after")
    def check_model_settings(self) -> Self:
        if self.uses_model and self.llm_api_key is None:
            raise ValueError(f"LLM_PROVIDER={self.llm_provider} needs LLM_API_KEY")
        if self.resolved_llm_provider == "openai" and not self.llm_model:
            raise ValueError("Set LLM_MODEL to the model to call, for example gpt-5-mini")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
