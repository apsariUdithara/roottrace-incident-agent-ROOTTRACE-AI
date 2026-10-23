"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal, Self

from cryptography.fernet import Fernet
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REQUIRED_IN_PRODUCTION = ("nebius_api_key", "jwt_secret", "fernet_key")
MIN_JWT_SECRET_LENGTH = 32  # HS256 wants a key at least as long as its 256-bit output


class Settings(BaseSettings):
    # Empty values (e.g. `JWT_SECRET=` copied from .env.example) count as unset.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    app_env: Literal["development", "test", "production"] = "development"

    # Nebius Token Factory (OpenAI-compatible).
    # IDs confirmed with GET /v1/models on 2026-10-11; they are case-sensitive.
    nebius_api_key: SecretStr | None = None
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nemotron_ultra: str = "nvidia/Nemotron-3-Ultra-550b-a55b"
    nemotron_super: str = "nvidia/nemotron-3-super-120b-a12b"
    nemotron_nano: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    embed_model: str = "Qwen/Qwen3-Embedding-8B"
    embed_dimensions: int = 1024

    # PostgreSQL + pgvector
    database_url: str = "postgresql+asyncpg://roottrace:roottrace@localhost:5432/roottrace"
    readonly_database_url: str | None = None

    # Auth and secrets
    jwt_secret: SecretStr | None = None
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    fernet_key: SecretStr | None = None
    frontend_origin: str = "http://localhost:5173"
    # Fine-grained GitHub token limited to the demo repo (Contents + Pull requests: write).
    # Used only after an engineer approves a fix, to open a draft PR.
    github_token: SecretStr | None = None

    # Agent
    tool_transport: Literal["direct", "mcp"] = "direct"
    sandbox_runner: Literal["nebius", "docker"] = "nebius"

    @model_validator(mode="after")
    def _check_secrets(self) -> Self:
        if self.app_env == "production":
            missing = [name for name in REQUIRED_IN_PRODUCTION if getattr(self, name) is None]
            if missing:
                raise ValueError(f"Missing required settings in production: {', '.join(missing)}")
            if len(self.jwt_secret.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
                raise ValueError(f"JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters")
        if self.fernet_key is not None:
            try:
                Fernet(self.fernet_key.get_secret_value())
            except ValueError as exc:
                raise ValueError("FERNET_KEY must be a key made by Fernet.generate_key()") from exc
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
