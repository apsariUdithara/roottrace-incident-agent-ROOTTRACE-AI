"""CRUD for the current user's projects. Every other project-scoped route hangs off these."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, OwnedProject, owned_select
from app.core.db import SessionDep
from app.models import Project
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])

_NAME_TAKEN = "uq_projects_owner_id_name"


async def _commit_or_409(session: SessionDep) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        if _NAME_TAKEN not in str(exc.orig):
            raise
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You already have a project with this name"
        ) from exc


@router.get("")
async def list_projects(user: CurrentUser, session: SessionDep) -> list[ProjectOut]:
    projects = await session.scalars(
        owned_select(Project, user).order_by(Project.created_at.desc(), Project.name)
    )
    return [ProjectOut.model_validate(project) for project in projects]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, user: CurrentUser, session: SessionDep) -> ProjectOut:
    project = Project(owner_id=user.id, name=body.name, description=body.description)
    session.add(project)
    await _commit_or_409(session)
    return ProjectOut.model_validate(project)


@router.get("/{project_id}")
async def get_project(project: OwnedProject) -> ProjectOut:
    return ProjectOut.model_validate(project)


@router.patch("/{project_id}")
async def update_project(
    body: ProjectUpdate, project: OwnedProject, session: SessionDep
) -> ProjectOut:
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(project, field, value)
    await _commit_or_409(session)
    return ProjectOut.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project: OwnedProject, session: SessionDep) -> None:
    """Also deletes everything in the project (database-level cascade)."""
    await session.delete(project)
    await session.commit()
