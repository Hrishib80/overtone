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

    # `local` writes into MEDIA_ROOT and needs no cloud account, which is what
    # makes the app workable offline. Production refuses it — see storage.py.
    storage_provider: Literal["local", "supabase"] = "local"
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_bucket: str = "profile-media"

    # Speech-to-text. Telugu accuracy is why this is Sarvam rather than a
    # general-purpose provider; see the architecture doc, model stack.
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    # saarika transcribes in the language spoken; saaras translates to
    # English, which would undo the point of a multilingual embedder.
    # Pinned and configurable because Sarvam retires versions.
    sarvam_model: str = "saarika:v2.5"

    # Worker. Real models are opt-in outside production: loading one can pull
    # gigabytes from HuggingFace, which should never happen because someone ran
    # a status command.
    use_real_models: bool | None = None
    worker_poll_seconds: float = 2.0
    worker_batch_size: int = 5
    job_max_attempts: int = 5

    # Uploads. Enforced when the signed URL is issued, not after the bytes
    # arrive — the whole point of direct-to-storage is that we never hold them.
    max_photo_bytes: int = 8 * 1024 * 1024
    max_audio_bytes: int = 2 * 1024 * 1024
    upload_url_ttl_seconds: int = 600

    media_root: Path = Path("media_uploads")

    # Kept as a raw string: pydantic-settings JSON-decodes list-typed fields at
    # the source, before any validator runs, so `ALLOWED_ORIGINS=*` would fail
    # to parse. Split in the `allowed_origins` property instead.
    allowed_origins_raw: str = Field(default="*", alias="ALLOWED_ORIGINS")

    # Where a link in the app should point when one is built server-side.
    # Not derived from the request, because a URL built from a Host header is
    # a URL an attacker can aim elsewhere.
    public_web_url: str = "http://localhost:5173"

    # Whether a finished profile waits for an admin before it is shown to
    # anyone. On by default; an invite code from an approved member skips the
    # wait either way. Turning it off is how signup opens up again later without
    # a code change.
    require_approval: bool = True

    # Chat fan-out between workers. Unset means one process, which is the
    # right deployment at campus scale — see backend/bus.py for why that is a
    # supported mode rather than a missing feature.
    redis_url: str = ""

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
    def real_models_enabled(self) -> bool:
        if self.use_real_models is not None:
            return self.use_real_models
        return self.is_production

    @property
    def db_is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql", "postgres"))

    @property
    def db_connect_args(self) -> dict:
        """Supabase's transaction pooler (pgbouncer) cannot hold prepared
        statements between checkouts, so asyncpg's statement cache must be off
        or every query after the first fails with a DuplicatePreparedStatement.
        """
        if not self.db_is_postgres:
            return {}
        args: dict = {"statement_cache_size": 0}
        if "pooler.supabase.com" in self.database_url:
            # asyncpg also caches per-connection type introspection, which the
            # pooler invalidates the same way.
            args["prepared_statement_cache_size"] = 0
            args["server_settings"] = {"jit": "off"}
        return args

    @property
    def emit_json_logs(self) -> bool:
        return self.is_production if self.log_json is None else self.log_json


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
