"""Request and response bodies for /projects/{id}/memories (long-term memory)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models import MemoryAuthor, MemoryCategory, MemoryStatus

MemoryContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: MemoryCategory
    content: str
    source_ref: str | None
    source_investigation_id: uuid.UUID | None
    created_by: MemoryAuthor  # the UI labels agent-written memories, so they aren't trusted blindly
    status: MemoryStatus
    created_at: datetime
    updated_at: datetime


class MemoryUpdate(BaseModel):
    """Only the fields you send are changed. A new `content` is re-embedded."""

    content: MemoryContent | None = None
    category: MemoryCategory | None = None
    status: MemoryStatus | None = None  # "archived": kept, but no longer recalled by the agent
