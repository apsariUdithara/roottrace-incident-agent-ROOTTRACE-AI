"""CRUD for a project's incident history, which the agent searches for similar past incidents."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import OwnedProject
from app.core.errors import not_implemented
from app.schemas.incident import IncidentCreate, IncidentOut, IncidentUpdate

router = APIRouter(prefix="/projects/{project_id}/incidents", tags=["incidents"])


@router.get("")
async def list_incidents(project: OwnedProject) -> list[IncidentOut]:
    """Newest first."""
    raise not_implemented("B-303")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_incident(body: IncidentCreate, project: OwnedProject) -> IncidentOut:
    """409 if the project already has an incident with this key."""
    raise not_implemented("B-303")


@router.get("/{incident_id}")
async def get_incident(incident_id: uuid.UUID, project: OwnedProject) -> IncidentOut:
    raise not_implemented("B-303")


@router.patch("/{incident_id}")
async def update_incident(
    incident_id: uuid.UUID, body: IncidentUpdate, project: OwnedProject
) -> IncidentOut:
    raise not_implemented("B-303")


@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_incident(incident_id: uuid.UUID, project: OwnedProject) -> None:
    raise not_implemented("B-303")
