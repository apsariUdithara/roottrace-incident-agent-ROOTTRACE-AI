"""Dependencies shared by the routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.db import SessionDep
from app.core.security import InvalidTokenError, TokenType, decode_token
from app.models import User

# auto_error=False: we raise our own 401 (FastAPI's default for a missing header is 403).
_bearer = HTTPBearer(auto_error=False)


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """The user behind `Authorization: Bearer <access token>`, or 401."""
    if credentials is None:
        raise unauthorized("Not authenticated")
    try:
        claims = decode_token(credentials.credentials, TokenType.ACCESS)
    except InvalidTokenError as exc:
        raise unauthorized("Invalid or expired token") from exc
    user = await session.get(User, claims.user_id)
    if user is None:  # deleted since the token was issued
        raise unauthorized("Invalid or expired token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
