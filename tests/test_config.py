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


def test_the_api_will_not_run_real_models_in_process():
    """Gigabytes of models inside the API is the one worker placement that
    takes the site down with the first photo."""
    with pytest.raises(ValidationError, match="RUN_WORKER_IN_API"):
        Settings(**PROD | {"run_worker_in_api": True, "use_real_models": True})
    settings = Settings(**PROD | {"run_worker_in_api": True, "use_real_models": False})
    assert settings.run_worker_in_api and not settings.real_models_enabled


def test_alembic_survives_a_percent_encoded_password():
    """Supabase passwords with `@` or `#` are percent-encoded in the URL, and
    Alembic's config is a ConfigParser that reads `%` as interpolation."""
    from alembic.config import Config

    url = "postgresql+asyncpg://postgres.abc:p%40ss%23word@aws-0-x.pooler.supabase.com:5432/postgres"
    config = Config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert config.get_main_option("sqlalchemy.url") == url
    assert config.get_section(config.config_ini_section)["sqlalchemy.url"] == url


@pytest.mark.parametrize(
    "pasted",
    [
        "https://abc.supabase.co",
        "https://abc.supabase.co/",
        "https://abc.supabase.co/rest/v1/",
        " https://abc.supabase.co/rest/v1 ",
    ],
)
def test_the_supabase_url_is_the_project_origin_whatever_was_pasted(pasted):
    """The dashboard's Data API page shows the URL with `/rest/v1/` on the end,
    and storage calls built on that 404."""
    assert Settings(**PROD | {"supabase_url": pasted}).supabase_url == "https://abc.supabase.co"
