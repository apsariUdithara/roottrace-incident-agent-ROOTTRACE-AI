"""Start investigations: create the row, wire tools and events, run the graph.

Two ways in:
- `run_investigation`: create and run in the caller's session (scripts, evaluation).
- `run_existing`: run an investigation a route already created, in a session of its own. The API
  starts it as a background task, because a request's session ends with the request.
"""

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.events import EventBus, Listener
from app.agent.graph import AgentState, Investigator
from app.core.db import SessionLocal
from app.llm.router import LLMRouter, get_router
from app.models import Investigation, Repository
from app.tools import ToolContext


async def _run(
    session: AsyncSession,
    router: LLMRouter,
    investigation: Investigation,
    ref: str | None,
    reported_at: datetime | None,
    listeners: list[Listener] | None,
    sandbox=None,
) -> AgentState | None:
    repo = await session.scalar(
        select(Repository).where(Repository.project_id == investigation.project_id)
    )
    if repo is None or not repo.local_path:
        raise ValueError("this project has no indexed repository")
    bus = await EventBus.resume(session, investigation.id)
    bus.listeners.extend(listeners or [])
    tools = ToolContext(
        session,
        router,
        investigation.project_id,
        Path(repo.local_path),
        ref=ref or repo.default_branch,
    )
    agent = Investigator(
        investigation,
        tools,
        bus,
        reported_at,
        sandbox=sandbox,
        test_command=repo.test_command,
        setup_command=repo.setup_command,
    )
    state = await agent.run()
    await session.commit()
    return state


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
    investigation = Investigation(project_id=project_id, user_id=user_id, question=question)
    session.add(investigation)
    await session.commit()
    state = await _run(session, router, investigation, ref, reported_at, listeners)
    return investigation, state


async def run_existing(
    investigation_id: uuid.UUID, ref: str | None = None, reported_at: datetime | None = None
) -> None:
    """Run an already-created investigation to the end, in its own database session."""
    async with SessionLocal() as session:
        investigation = await session.get(Investigation, investigation_id)
        await _run(session, get_router(), investigation, ref, reported_at, None)
