"""Runtime settings, read from environment variables.

Every variable listed in REQUIRED_ENV must be set in each deploy environment
(see deploy/production.env).
"""
import os

REQUIRED_ENV = [
    "PAYSTREAM_DATABASE_URL",
    "PAYSTREAM_GATEWAY_API_KEY",
    "PAYSTREAM_WEBHOOK_SECRET",
]


class MissingSetting(RuntimeError):
    pass


def get(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise MissingSetting(f"environment variable {name} is not set") from None


def gateway_api_key() -> str:
    return get("PAYSTREAM_GATEWAY_API_KEY")


def webhook_secret() -> str:
    return get("PAYSTREAM_WEBHOOK_SECRET")
