"""View, correct and remove what the agent remembers about a project."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import OwnedProject
from app.core.db import SessionDep
from app.llm.router import get_router
from app.models import Memory, MemoryAuthor, MemoryCategory, MemoryStatus
from app.schemas.memory import MemoryOut, MemoryUpdate

router = APIRouter(prefix="/projects/{project_id}/memories", tags=["memories"])


@router.get("")
async def list_memories(
    project: OwnedProject,
    session: SessionDep,
    category: MemoryCategory | None = None,
    status_: Annotated[MemoryStatus | None, Query(alias="status")] = None,
) -> list[MemoryOut]:
    """Newest first."""
    query = select(Memory).where(Memory.project_id == project.id)
    if category:
        query = query.where(Memory.category == category)
    if status_:
        query = query.where(Memory.status == status_)
    rows = await session.scalars(query.order_by(Memory.created_at.desc()))
    return [MemoryOut.model_validate(m) for m in rows]


async def owned_memory(session, project, memory_id: uuid.UUID) -> Memory:
    memory = await session.get(Memory, memory_id)
    if memory is None or memory.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    return memory


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: uuid.UUID, body: MemoryUpdate, project: OwnedProject, session: SessionDep
) -> MemoryOut:
    memory = await owned_memory(session, project, memory_id)
    if body.content is not None and body.content != memory.content:
        memory.content = body.content
        memory.embedding = (await get_router().embed([body.content], name="embed memory"))[0]
        memory.created_by = MemoryAuthor.USER  # a person wrote this version, not the agent
    if body.category is not None:
        memory.category = body.category
    if body.status is not None:
        memory.status = body.status
    await session.commit()
    await session.refresh(memory)
    return MemoryOut.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: uuid.UUID, project: OwnedProject, session: SessionDep) -> None:
    """Deletes it for good. To keep it but stop the agent recalling it, PATCH `status: archived`."""
    await session.delete(await owned_memory(session, project, memory_id))
    await session.commit()
