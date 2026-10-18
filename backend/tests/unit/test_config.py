from typing import Any

import pytest

from app.core.config import Settings


def test_allowed_origins_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000, https://smartroute.example ,")
    assert Settings().allowed_origins == ["http://localhost:3000", "https://smartroute.example"]


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.allowed_origins == []
    assert settings.app_env == "development"


PRODUCTION: dict[str, Any] = {
    "_env_file": None,
    "app_env": "production",
    "key_hashing_secret": "x" * 64,
    "allowed_origins": ["https://smartroute.example"],
    "database_url": "postgresql+asyncpg://app:a-real-password@db:5432/smartroute",
}


def test_production_settings_accept_a_real_deployment() -> None:
    assert Settings(**PRODUCTION).app_env == "production"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"key_hashing_secret": "development-only-secret"}, "must be set"),
        ({"key_hashing_secret": "short"}, "at least 32"),
        ({"allowed_origins": ["*"]}, "https origins"),
        ({"allowed_origins": ["http://smartroute.example"]}, "https origins"),
        (
            {"database_url": "postgresql+asyncpg://smartroute:smartroute@db:5432/smartroute"},
            "development password",
        ),
    ],
)
def test_production_refuses_laptop_settings(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(**{**PRODUCTION, **override})
