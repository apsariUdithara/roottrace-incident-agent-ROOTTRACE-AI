import secrets

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import REQUIRED_IN_PRODUCTION, Settings

SECRET_ENV_VARS = [name.upper() for name in REQUIRED_IN_PRODUCTION]


def set_production_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-key")
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_production_refuses_missing_secrets(monkeypatch: pytest.MonkeyPatch, value: str | None):
    monkeypatch.setenv("APP_ENV", "production")
    for name in SECRET_ENV_VARS:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match="Missing required settings in production"):
        Settings(_env_file=None)


def test_production_starts_with_secrets(monkeypatch: pytest.MonkeyPatch):
    set_production_secrets(monkeypatch)

    assert Settings(_env_file=None).app_env == "production"


def test_production_refuses_a_short_jwt_secret(monkeypatch: pytest.MonkeyPatch):
    set_production_secrets(monkeypatch)
    monkeypatch.setenv("JWT_SECRET", "too-short")

    with pytest.raises(ValidationError, match="JWT_SECRET must be at least 32 characters"):
        Settings(_env_file=None)


def test_refuses_a_malformed_fernet_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FERNET_KEY", "not-a-fernet-key")

    with pytest.raises(ValidationError, match="FERNET_KEY must be a key made by"):
        Settings(_env_file=None)
