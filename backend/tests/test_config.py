import pytest
from pydantic import ValidationError

from app.core.config import REQUIRED_IN_PRODUCTION, Settings

SECRET_ENV_VARS = [name.upper() for name in REQUIRED_IN_PRODUCTION]


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
    monkeypatch.setenv("APP_ENV", "production")
    for name in SECRET_ENV_VARS:
        monkeypatch.setenv(name, "set")

    assert Settings(_env_file=None).app_env == "production"
