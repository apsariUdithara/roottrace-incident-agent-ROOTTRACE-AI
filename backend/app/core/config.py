"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REQUIRED_IN_PRODUCTION = ("nebius_api_key", "jwt_secret", "fernet_key")


class Settings(BaseSettings):
    # Empty values (e.g. `JWT_SECRET=` copied from .env.example) count as unset.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    app_env: Literal["development", "test", "production"] = "development"

    # Nebius Token Factory (OpenAI-compatible). Verify model IDs with GET /v1/models.
    nebius_api_key: SecretStr | None = None
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nemotron_ultra: str = "nvidia/nemotron-3-ultra-550b-a55b"
    nemotron_super: str = "nvidia/nemotron-3-super-120b-a12b"
    nemotron_nano: str = "nvidia/nemotron-3-nano-30b-a3b"
    embed_model: str | None = None
    embed_dimensions: int | None = None

    # PostgreSQL + pgvector
    database_url: str = "postgresql+asyncpg://roottrace:roottrace@localhost:5432/roottrace"
    readonly_database_url: str | None = None

    # Auth and secrets
    jwt_secret: SecretStr | None = None
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    fernet_key: SecretStr | None = None
    frontend_origin: str = "http://localhost:5173"

    # Agent
    tool_transport: Literal["direct", "mcp"] = "direct"
    sandbox_runner: Literal["nebius", "docker"] = "nebius"

    @model_validator(mode="after")
    def _require_secrets_in_production(self) -> Self:
        if self.app_env == "production":
            missing = [name for name in REQUIRED_IN_PRODUCTION if getattr(self, name) is None]
            if missing:
                raise ValueError(f"Missing required settings in production: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
