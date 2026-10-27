"""View, correct and remove what the agent remembers about a project."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import OwnedProject
from app.core.errors import not_implemented
from app.models import MemoryCategory, MemoryStatus
from app.schemas.memory import MemoryOut, MemoryUpdate

router = APIRouter(prefix="/projects/{project_id}/memories", tags=["memories"])


@router.get("")
async def list_memories(
    project: OwnedProject,
    category: MemoryCategory | None = None,
    status_: Annotated[MemoryStatus | None, Query(alias="status")] = None,
) -> list[MemoryOut]:
    """Newest first."""
    raise not_implemented("B-703")


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: uuid.UUID, body: MemoryUpdate, project: OwnedProject
) -> MemoryOut:
    raise not_implemented("B-703")


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: uuid.UUID, project: OwnedProject) -> None:
    """Deletes it for good. To keep it but stop the agent recalling it, PATCH `status: archived`."""
    raise not_implemented("B-703")
