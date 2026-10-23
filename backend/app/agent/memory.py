"""Long-term memory: short lessons saved after an investigation, recalled as hints next time.

Memories are HINTS, never evidence: a recalled memory can tell the agent where to look, but it
is never cited as a fact and never raises the confidence score. A memory can be wrong or out of
date; counting it as evidence would let an old mistake confirm itself ("memory poisoning").
"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMRouter
from app.models import Memory, MemoryAuthor, MemoryCategory, MemoryStatus

RECALL_K = 3
MAX_RECALL_DISTANCE = 0.6  # cosine distance: further than this is not relevant enough
DUPLICATE_DISTANCE = 0.08  # closer than this to an existing memory counts as the same lesson


class Lesson(BaseModel):
    category: Literal["previous_incident", "project_fact", "arch_decision"]
    content: str = Field(max_length=400)


class Lessons(BaseModel):
    lessons: list[Lesson] = Field(max_length=3)


SAVE_PROMPT = """An incident investigation just finished.
Question: {question}
Root cause (verified): {root_cause}
Fix: {fix}
Key facts:
{facts}

Write 1-3 short lessons worth remembering for FUTURE incidents in this project, each one
sentence, specific and durable (true beyond today): e.g. a recurring failure mode and what
causes it, a project limit or rule, where the relevant setting lives. No dates of this incident,
no trace IDs, nothing that only matters for this one event.
category: previous_incident (a failure mode that happened), project_fact (a limit, setting or
rule of the system), or arch_decision (a design choice and its reason)."""


async def recall(
    session: AsyncSession, router: LLMRouter, project_id: uuid.UUID, query: str
) -> list[Memory]:
    """The few active memories closest in meaning to `query`."""
    [vector] = await router.embed([query], name="embed memory query")
    distance = Memory.embedding.cosine_distance(vector)
    rows = await session.execute(
        select(Memory)
        .where(
            Memory.project_id == project_id,
            Memory.status == MemoryStatus.ACTIVE,
            Memory.embedding.is_not(None),
            distance <= MAX_RECALL_DISTANCE,
        )
        .order_by(distance)
        .limit(RECALL_K)
    )
    return list(rows.scalars())


async def save_lessons(
    session: AsyncSession,
    router: LLMRouter,
    project_id: uuid.UUID,
    investigation_id: uuid.UUID,
    lessons: list[Lesson],
) -> list[Memory]:
    """Store lessons, skipping any that repeat an existing memory."""
    if not lessons:
        return []
    vectors = await router.embed([lesson.content for lesson in lessons], name="embed memories")
    saved = []
    for lesson, vector in zip(lessons, vectors, strict=True):
        duplicate = await session.scalar(
            select(Memory.id).where(
                Memory.project_id == project_id,
                Memory.status == MemoryStatus.ACTIVE,
                Memory.embedding.cosine_distance(vector) < DUPLICATE_DISTANCE,
            )
        )
        if duplicate:
            continue
        memory = Memory(
            project_id=project_id,
            category=MemoryCategory(lesson.category),
            content=lesson.content,
            source_ref=f"investigation:{investigation_id}",
            source_investigation_id=investigation_id,
            embedding=vector,
            created_by=MemoryAuthor.AGENT,
        )
        session.add(memory)
        saved.append(memory)
    await session.flush()
    return saved


def as_hints(memories: list[Memory]) -> str:
    if not memories:
        return ""
    lines = "\n".join(f"- ({m.category}) {m.content}" for m in memories)
    return (
        "\nNotes from past investigations of this project. These are HINTS about where to look, "
        "not evidence: they may be outdated. Verify anything you rely on with the tools.\n"
        f"{lines}\n"
    )
