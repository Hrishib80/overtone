"""Configuration guardrails.

These encode the rules that stop a bad production deploy: no wildcard CORS
alongside credentialed requests, no weak signing key, no missing database.
"""

import pytest
from pydantic import ValidationError

from backend.config import Settings

PROD = {
    "environment": "production",
    "database_url": "postgresql+asyncpg://u:p@db/overtone",
    "jwt_secret_key": "x" * 48,
    "supabase_url": "https://project.supabase.co",
    "supabase_key": "service-key",
    "ALLOWED_ORIGINS": "https://overtone.example",
}


def test_production_config_is_accepted():
    settings = Settings(**PROD)
    assert settings.is_production
    assert settings.allowed_origins == ["https://overtone.example"]
    assert settings.emit_json_logs is True


def test_production_rejects_wildcard_origins():
    with pytest.raises(ValidationError, match="ALLOWED_ORIGINS"):
        Settings(**PROD | {"ALLOWED_ORIGINS": "*"})


def test_production_rejects_a_short_signing_key():
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(**PROD | {"jwt_secret_key": "too-short"})


def test_production_requires_a_database_url():
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(**PROD | {"database_url": ""})


def test_production_requires_supabase_credentials():
    with pytest.raises(ValidationError, match="SUPABASE_KEY"):
        Settings(**PROD | {"supabase_key": ""})


def test_origins_are_split_and_trailing_slashes_trimmed():
    settings = Settings(**PROD | {"ALLOWED_ORIGINS": "https://a.example/, https://b.example"})
    assert settings.allowed_origins == ["https://a.example", "https://b.example"]


def test_development_tolerates_missing_values():
    """Development must stay runnable before every secret is filled in."""
    settings = Settings(environment="development", database_url="", jwt_secret_key="")
    assert not settings.is_production
    assert settings.emit_json_logs is False
