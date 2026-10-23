"""Start one investigation: create its row, wire tools and events, run the graph."""

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.events import EventBus, Listener
from app.agent.graph import AgentState, Investigator
from app.llm.router import LLMRouter
from app.models import Investigation, Repository
from app.tools import ToolContext


async def run_investigation(
    session: AsyncSession,
    router: LLMRouter,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    question: str,
    ref: str = "HEAD",
    reported_at: datetime | None = None,
    listeners: list[Listener] | None = None,
) -> tuple[Investigation, AgentState | None]:
    repo = await session.scalar(select(Repository).where(Repository.project_id == project_id))
    if repo is None or not repo.local_path:
        raise ValueError("this project has no indexed repository")
    investigation = Investigation(project_id=project_id, user_id=user_id, question=question)
    session.add(investigation)
    await session.commit()

    bus = EventBus(session, investigation.id)
    bus.listeners.extend(listeners or [])
    tools = ToolContext(session, router, project_id, Path(repo.local_path), ref=ref)
    state = await Investigator(investigation, tools, bus, reported_at).run()
    await session.commit()
    return investigation, state
