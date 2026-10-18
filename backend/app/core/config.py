"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    route_cache_near_term_ttl_s: int = Field(default=60, gt=0)
    route_cache_empty_ttl_s: int = Field(default=30, gt=0)
    single_flight_lock_ttl_ms: int = Field(default=10_000, gt=0)
    single_flight_wait_s: float = Field(default=3.0, gt=0)

    rate_limit_route_search_per_minute: int = Field(default=20, ge=1)
    rate_limit_interpret_per_minute: int = Field(default=10, ge=1)

    # Language model. "baseline" is a keyword-rule stand-in, not a model.
    llm_provider: Literal["disabled", "baseline"] = "disabled"
    llm_timeout_s: float = Field(default=8.0, gt=0)
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

    ingest_elevators_interval_s: int = Field(default=120, ge=10)
    ingest_realtime_interval_s: int = Field(default=60, ge=10)
    ingest_static_interval_s: int = Field(default=3600, ge=60)
    ingest_retention_interval_s: int = Field(default=86_400, ge=60)
    ingest_fetch_timeout_s: float = Field(default=20.0, gt=0)

    retention_events_days: int = Field(default=90, ge=1)
    retention_sync_runs_days: int = Field(default=30, ge=1)
    retention_feedback_comment_days: int = Field(default=365, ge=1)
    retention_feedback_days: int = Field(default=730, ge=1)

    health_check_timeout_s: float = Field(default=1.0, gt=0)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, as environment variables provide."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def require_real_secret_in_production(self) -> Self:
        if self.app_env == "production" and (
            self.key_hashing_secret.get_secret_value() == "development-only-secret"
        ):
            raise ValueError("KEY_HASHING_SECRET must be set in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
