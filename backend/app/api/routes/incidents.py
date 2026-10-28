"""CRUD for a project's incident history, which the agent searches for similar past incidents.

Each change also updates the search index (`search_previous_incidents`) in the same
transaction, so the agent sees exactly the incidents listed here. Indexing embeds the incident's
text: one small call to the embedding model, skipped when the text is unchanged.
"""

import logging
import uuid
from typing import Annotated

import openai
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OwnedProject
from app.core.db import SessionDep
from app.llm.router import LLMRouter, get_router
from app.models import Incident, Project
from app.rag.ingest import index_incident, unindex_incident
from app.schemas.incident import IncidentCreate, IncidentOut, IncidentUpdate

router = APIRouter(prefix="/projects/{project_id}/incidents", tags=["incidents"])
logger = logging.getLogger(__name__)

RouterDep = Annotated[LLMRouter, Depends(get_router)]
_KEY_TAKEN = "uq_incidents_project_id_key"
# Null can't clear these, so a null in a PATCH leaves them as they are.
_REQUIRED = {"key", "title", "tags", "status"}


async def get_incident_or_404(
    session: AsyncSession, project: Project, incident_id: uuid.UUID
) -> Incident:
    incident = await session.scalar(
        select(Incident).where(Incident.id == incident_id, Incident.project_id == project.id)
    )
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return incident


async def flush_or_409(session: AsyncSession, key: str) -> None:
    """Writes the row before indexing, so a taken key costs no embedding call."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if _KEY_TAKEN not in str(exc.orig):
            raise
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This project already has an incident {key}"
        ) from exc


async def index_or_502(session: AsyncSession, llm: LLMRouter, incident: Incident) -> None:
    try:
        await index_incident(session, llm, incident)
    except (openai.APIError, TimeoutError) as exc:
        # Nothing is saved: an incident the agent can't find would be worse than a retry.
        logger.warning(
            "incidents: indexing %s failed: %s: %s", incident.key, type(exc).__name__, exc
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't index the incident for search, so it wasn't saved. "
            "Please try again in a moment.",
        ) from exc


@router.get("")
async def list_incidents(project: OwnedProject, session: SessionDep) -> list[IncidentOut]:
    """Newest first: by when it happened, or else when it was added."""
    incidents = await session.scalars(
        select(Incident)
        .where(Incident.project_id == project.id)
        .order_by(func.coalesce(Incident.occurred_at, Incident.created_at).desc(), Incident.key)
    )
    return [IncidentOut.model_validate(incident) for incident in incidents]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_incident(
    body: IncidentCreate, project: OwnedProject, session: SessionDep, llm: RouterDep
) -> IncidentOut:
    """409 if the project already has an incident with this key."""
    incident = Incident(project_id=project.id, **body.model_dump())
    session.add(incident)
    await flush_or_409(session, incident.key)
    await index_or_502(session, llm, incident)
    await session.commit()
    await session.refresh(incident)
    return IncidentOut.model_validate(incident)


@router.get("/{incident_id}")
async def get_incident(
    incident_id: uuid.UUID, project: OwnedProject, session: SessionDep
) -> IncidentOut:
    return IncidentOut.model_validate(await get_incident_or_404(session, project, incident_id))


@router.patch("/{incident_id}")
async def update_incident(
    incident_id: uuid.UUID,
    body: IncidentUpdate,
    project: OwnedProject,
    session: SessionDep,
    llm: RouterDep,
) -> IncidentOut:
    incident = await get_incident_or_404(session, project, incident_id)
    old_key = incident.key
    for field in body.model_fields_set:
        value = getattr(body, field)
        if value is not None or field not in _REQUIRED:
            setattr(incident, field, value)
    await flush_or_409(session, incident.key)
    if incident.key != old_key:
        await unindex_incident(session, project.id, old_key)
    await index_or_502(session, llm, incident)
    await session.commit()
    return IncidentOut.model_validate(incident)


@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_incident(
    incident_id: uuid.UUID, project: OwnedProject, session: SessionDep
) -> None:
    """Also removes it from the search index. Investigations that linked it keep their results."""
    incident = await get_incident_or_404(session, project, incident_id)
    await unindex_incident(session, project.id, incident.key)
    await session.delete(incident)
    await session.commit()
