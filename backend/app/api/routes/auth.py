"""Register, log in, refresh, log out, and who-am-I.

The access token is returned in the body (the frontend keeps it in memory). The refresh token
is an httpOnly cookie, so page scripts can never read it.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, unauthorized
from app.core.config import get_settings
from app.core.db import SessionDep
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    InvalidTokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "roottrace_refresh"


def _cookie_options() -> dict:
    return {
        "path": "/",
        "httponly": True,  # not readable from JavaScript
        "secure": get_settings().app_env == "production",  # HTTPS only in production
        "samesite": "strict",  # never sent on cross-site requests, which blocks CSRF
    }


def _issue_tokens(user: User, response: Response) -> TokenResponse:
    """New access token in the body, new refresh token in the cookie (rotated on every call)."""
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        create_refresh_token(user.id),
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        **_cookie_options(),
    )
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=settings.access_token_minutes * 60,
        user=UserOut.model_validate(user),
    )


def _email_taken() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: SessionDep, response: Response) -> TokenResponse:
    if await session.scalar(select(User.id).where(User.email == body.email)):
        raise _email_taken()
    user = User(email=body.email, password_hash=await hash_password(body.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:  # the same email registered at the same moment
        raise _email_taken() from exc
    return _issue_tokens(user, response)


@router.post("/login")
async def login(body: LoginRequest, session: SessionDep, response: Response) -> TokenResponse:
    user = await session.scalar(select(User).where(User.email == body.email))
    # Always run one argon2 check, so an unknown email can't be detected by timing.
    password_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    if not await verify_password(password_hash, body.password) or user is None:
        raise unauthorized("Incorrect email or password")
    if password_needs_rehash(user.password_hash):
        user.password_hash = await hash_password(body.password)
        await session.commit()
    return _issue_tokens(user, response)


@router.post("/refresh")
async def refresh(
    session: SessionDep,
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> TokenResponse:
    if refresh_token is None:
        raise unauthorized("Not authenticated")
    try:
        claims = decode_token(refresh_token, TokenType.REFRESH)
    except InvalidTokenError as exc:
        raise unauthorized("Session expired, please log in again") from exc
    user = await session.get(User, claims.user_id)
    if user is None:
        raise unauthorized("Session expired, please log in again")
    return _issue_tokens(user, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Clears the refresh cookie. The frontend drops its access token, which expires on its own
    within ACCESS_TOKEN_MINUTES (tokens aren't revoked server-side)."""
    response.delete_cookie(REFRESH_COOKIE, **_cookie_options())


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
