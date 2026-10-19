"""Session tokens. All timestamps are timezone-aware UTC (see docs/authentication.md)."""
import secrets
from datetime import UTC, datetime, timedelta

TOKEN_TTL = timedelta(hours=1)


class InvalidToken(Exception):
    pass


def issue_token(user_id: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    return {"sub": user_id, "value": secrets.token_urlsafe(24), "expires_at": now + TOKEN_TTL}


def is_expired(token: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return now >= token["expires_at"]


def verify(token: dict | None) -> int:
    """Return the user id for a valid token, or raise InvalidToken (sent as HTTP 401)."""
    try:
        if token is None or is_expired(token):
            raise InvalidToken("expired or missing token")
        return token["sub"]
    except InvalidToken:
        raise
    except Exception as exc:  # malformed token data
        raise InvalidToken(str(exc)) from exc
