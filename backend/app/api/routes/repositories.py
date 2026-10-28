"""A project's connected GitHub repositories. The token is write-only: stored encrypted, never
returned.

One repository per project for now: the agent investigates a single codebase.
"""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OwnedProject
from app.core.db import SessionDep
from app.core.security import encrypt_secret
from app.models import Document, Project, Repository, SandboxStatus
from app.rag import indexing
from app.rag.ingest import REPO_TYPES
from app.schemas.repository import RepositoryCreate, RepositoryOut, RepositoryUpdate

router = APIRouter(prefix="/projects/{project_id}/repositories", tags=["repositories"])


async def get_repository(
    session: AsyncSession, project: Project, repository_id: uuid.UUID
) -> Repository:
    repository = await session.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.project_id == project.id
        )
    )
    if repository is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
    return repository


@router.get("")
async def list_repositories(project: OwnedProject, session: SessionDep) -> list[RepositoryOut]:
    repositories = await session.scalars(
        select(Repository)
        .where(Repository.project_id == project.id)
        .order_by(Repository.created_at)
    )
    return [RepositoryOut.model_validate(repository) for repository in repositories]


@router.post("", status_code=status.HTTP_201_CREATED)
async def connect_repository(
    body: RepositoryCreate, project: OwnedProject, session: SessionDep
) -> RepositoryOut:
    # Locks the project row, so a double-clicked "Connect" can't add two repositories.
    await session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    if await session.scalar(select(Repository.id).where(Repository.project_id == project.id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This project already has a repository. Disconnect it to connect another one.",
        )
    repository = Repository(
        project_id=project.id,
        provider=body.provider,
        url=body.url,
        default_branch=body.default_branch,
        encrypted_token=encrypt_secret(body.token.get_secret_value()) if body.token else None,
        setup_command=body.setup_command,
        test_command=body.test_command,
    )
    session.add(repository)
    await session.commit()
    await session.refresh(repository)
    return RepositoryOut.model_validate(repository)


@router.patch("/{repository_id}")
async def update_repository(
    repository_id: uuid.UUID, body: RepositoryUpdate, project: OwnedProject, session: SessionDep
) -> RepositoryOut:
    repository = await get_repository(session, project, repository_id)
    if "token" in body.model_fields_set:
        repository.encrypted_token = (
            encrypt_secret(body.token.get_secret_value()) if body.token else None
        )
    commands = (repository.setup_command, repository.test_command)
    if "setup_command" in body.model_fields_set:
        repository.setup_command = body.setup_command
    # These two can't be empty, so null leaves them as they are.
    if body.test_command is not None:
        repository.test_command = body.test_command
    if body.default_branch is not None:
        repository.default_branch = body.default_branch
    if (repository.setup_command, repository.test_command) != commands:
        repository.sandbox_status = SandboxStatus.UNKNOWN  # to be checked again with the new ones
    await session.commit()
    return RepositoryOut.model_validate(repository)


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_repository(
    repository_id: uuid.UUID, project: OwnedProject, session: SessionDep
) -> None:
    """Also removes what was indexed from it (its docs and code), and RootTrace's clone of it."""
    repository = await get_repository(session, project, repository_id)
    await session.execute(
        delete(Document).where(
            Document.project_id == project.id, Document.source_type.in_(REPO_TYPES)
        )
    )
    await session.delete(repository)
    await session.commit()
    await asyncio.to_thread(indexing.remove_clone, repository)
