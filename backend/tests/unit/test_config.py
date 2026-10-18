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
