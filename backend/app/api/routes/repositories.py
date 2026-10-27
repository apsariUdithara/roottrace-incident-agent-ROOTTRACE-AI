"""A project's connected GitHub repositories. The token is write-only: stored encrypted, never
returned."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import OwnedProject
from app.core.errors import not_implemented
from app.schemas.repository import RepositoryCreate, RepositoryOut, RepositoryUpdate

router = APIRouter(prefix="/projects/{project_id}/repositories", tags=["repositories"])


@router.get("")
async def list_repositories(project: OwnedProject) -> list[RepositoryOut]:
    raise not_implemented("B-301")


@router.post("", status_code=status.HTTP_201_CREATED)
async def connect_repository(body: RepositoryCreate, project: OwnedProject) -> RepositoryOut:
    raise not_implemented("B-301")


@router.patch("/{repository_id}")
async def update_repository(
    repository_id: uuid.UUID, body: RepositoryUpdate, project: OwnedProject
) -> RepositoryOut:
    raise not_implemented("B-301")


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_repository(repository_id: uuid.UUID, project: OwnedProject) -> None:
    """Also removes what was indexed from it."""
    raise not_implemented("B-301")
