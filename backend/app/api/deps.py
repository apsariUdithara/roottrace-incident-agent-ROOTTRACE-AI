"""Dependencies shared by the routes: who is calling, and which rows they may see."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, select

from app.core.db import SessionDep
from app.core.security import InvalidTokenError, TokenType, decode_token
from app.models import Base, Project, User

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


def owned_select[T: Base](model: type[T], user: User) -> Select[tuple[T]]:
    """SELECT rows of `model` that belong to one of `user`'s projects.

    The one place the scoping rule lives: every project-scoped query starts here, e.g.
    `owned_select(Investigation, user).where(Investigation.id == investigation_id)`.
    `model` is Project itself or any table with a `project_id` column.
    """
    if model is Project:
        return select(Project).where(Project.owner_id == user.id)
    return (
        select(model)
        .join(Project, model.project_id == Project.id)
        .where(Project.owner_id == user.id)
    )


async def get_owned_project(
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Project:
    """The `{project_id}` in the path, if the current user owns it.

    Someone else's project gives the same 404 as a missing one, so ids can't be probed.
    """
    project = await session.scalar(owned_select(Project, user).where(Project.id == project_id))
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


OwnedProject = Annotated[Project, Depends(get_owned_project)]
