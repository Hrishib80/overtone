"""Application settings.

Values come from the environment, or a .env file in development. Anything the
app cannot run safely without is validated at import time so a misconfigured
deploy fails immediately and loudly rather than at the first request.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    environment: Literal["development", "test", "production"] = "development"

    database_url: str = Field(default="", description="postgresql+asyncpg://...")
    db_pool_size: int = 10
    db_max_overflow: int = 20

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24

    supabase_url: str = ""
    supabase_key: str = ""
    supabase_bucket: str = "profile-media"

    media_root: Path = Path("media_uploads")

    # Kept as a raw string: pydantic-settings JSON-decodes list-typed fields at
    # the source, before any validator runs, so `ALLOWED_ORIGINS=*` would fail
    # to parse. Split in the `allowed_origins` property instead.
    allowed_origins_raw: str = Field(default="*", alias="ALLOWED_ORIGINS")

    log_level: str = "INFO"
    log_json: bool | None = None
    sentry_dsn: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @property
    def allowed_origins(self) -> list[str]:
        """Comma-separated in the environment, which is how hosts set it."""
        return [o.strip().rstrip("/") for o in self.allowed_origins_raw.split(",") if o.strip()]

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _check_required(self) -> Settings:
        # Tests supply their own database and secret; they must not be blocked
        # by production-shaped requirements.
        if self.is_test:
            return self

        required = {
            "DATABASE_URL": self.database_url,
            "JWT_SECRET_KEY": self.jwt_secret_key,
        }
        if self.is_production:
            required |= {
                "SUPABASE_URL": self.supabase_url,
                "SUPABASE_KEY": self.supabase_key,
            }

        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            message = f"Missing required setting(s): {', '.join(missing)}"
            if self.is_production:
                raise ValueError(message)
            print(f"WARNING: {message} — the app will not serve requests until these are set.")

        if self.is_production:
            if "*" in self.allowed_origins:
                raise ValueError(
                    "ALLOWED_ORIGINS must list explicit HTTPS origins in production; "
                    "credentialed browser requests cannot use a wildcard."
                )
            if len(self.jwt_secret_key) < 32:
                raise ValueError("JWT_SECRET_KEY must be at least 32 characters in production.")

        return self

    @property
    def emit_json_logs(self) -> bool:
        return self.is_production if self.log_json is None else self.log_json


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
