"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
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

    health_check_timeout_s: float = Field(default=1.0, gt=0)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, as environment variables provide."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
