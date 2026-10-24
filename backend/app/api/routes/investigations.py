"""Investigations over HTTP: start one, follow it live, ask follow-ups, approve its PR.

Starting returns at once (202) and the agent runs as a background task: an investigation takes
minutes, longer than a request should wait, and a page reload must not lose it. The page then
follows `/stream`, which replays the saved events and then polls for new ones, so a reconnect
with Last-Event-ID resumes exactly where it stopped.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.agent import followup, run
from app.agent.pr import PrError, open_pr
from app.api.deps import CurrentUser, OwnedProject, owned_select
from app.core.config import get_settings
from app.core.db import SessionDep, SessionLocal
from app.llm.router import get_router
from app.models import (
    Investigation,
    InvestigationEvent,
    InvestigationStatus,
    Patch,
    Repository,
)

router = APIRouter(tags=["investigations"])

POLL_S = 0.5  # how often the stream checks for new events
KEEPALIVE_S = 15  # an SSE comment this often keeps proxies from closing an idle stream
RUNNING: dict[uuid.UUID, asyncio.Task] = {}  # background runs; kept so they aren't GC'd


class InvestigationCreate(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    ref: str | None = Field(
        default=None, max_length=200, description="Deployed version; default: the default branch"
    )
    reported_at: datetime | None = None


class InvestigationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    question: str
    status: str
    root_cause: str | None
    confidence: float | None
    report: dict[str, Any] | None
    started_at: datetime
    finished_at: datetime | None
    total_tokens: int
    total_cost_usd: Decimal


class MessageIn(BaseModel):
    content: str = Field(min_length=2, max_length=2000)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str


class PrOpenedOut(BaseModel):
    url: str


async def get_owned_investigation(
    investigation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Investigation:
    """The `{investigation_id}` in the path, if it is in one of the user's projects (else 404)."""
    found = await session.scalar(
        owned_select(Investigation, user).where(Investigation.id == investigation_id)
    )
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Investigation not found")
    return found


OwnedInvestigation = Annotated[Investigation, Depends(get_owned_investigation)]


def start_in_background(investigation_id: uuid.UUID, ref: str | None, reported_at) -> None:
    task = asyncio.create_task(run.run_existing(investigation_id, ref, reported_at))
    RUNNING[investigation_id] = task
    task.add_done_callback(lambda _: RUNNING.pop(investigation_id, None))


ACTIVE = (InvestigationStatus.QUEUED, InvestigationStatus.RUNNING)
STALE_AFTER = timedelta(minutes=30)  # a "running" row older than this was cut off by a restart


def too_many(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail)


async def check_limits(session, user) -> None:
    """Guard rails for a public demo: one run at a time, a few per hour, a daily budget."""
    settings, now = get_settings(), datetime.now(UTC)
    active = Investigation.status.in_(ACTIVE) & (Investigation.started_at >= now - STALE_AFTER)

    if await session.scalar(select(func.count()).where(Investigation.user_id == user.id, active)):
        raise too_many("You already have an investigation running; wait for it to finish.")
    last_hour = await session.scalar(
        select(func.count()).where(
            Investigation.user_id == user.id, Investigation.started_at >= now - timedelta(hours=1)
        )
    )
    if last_hour >= settings.investigations_per_hour:
        raise too_many(
            f"Limit reached: {settings.investigations_per_hour} investigations per hour."
        )

    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spent, running = (
        await session.execute(
            select(
                func.coalesce(func.sum(Investigation.total_cost_usd), 0),
                func.count().filter(active),
            ).where(Investigation.started_at >= today)
        )
    ).one()
    # Running investigations haven't reported their cost yet: reserve their cap for them.
    committed = float(spent) + running * settings.investigation_cost_cap_usd
    if committed + settings.investigation_cost_cap_usd > settings.daily_budget_usd:
        raise too_many("The demo's daily budget is used up. Please try again tomorrow.")


@router.post("/projects/{project_id}/investigations", status_code=status.HTTP_202_ACCEPTED)
async def start_investigation(
    body: InvestigationCreate, project: OwnedProject, user: CurrentUser, session: SessionDep
) -> InvestigationOut:
    has_repo = await session.scalar(
        select(Repository.id).where(Repository.project_id == project.id)
    )
    if not has_repo:
        raise HTTPException(status.HTTP_409_CONFLICT, "Index a repository for this project first")
    await check_limits(session, user)
    investigation = Investigation(project_id=project.id, user_id=user.id, question=body.question)
    session.add(investigation)
    await session.commit()
    await session.refresh(investigation)
    start_in_background(investigation.id, body.ref, body.reported_at)
    return InvestigationOut.model_validate(investigation)


@router.get("/projects/{project_id}/investigations")
async def list_investigations(project: OwnedProject, session: SessionDep) -> list[InvestigationOut]:
    rows = await session.scalars(
        select(Investigation)
        .where(Investigation.project_id == project.id)
        .order_by(Investigation.started_at.desc())
    )
    return [InvestigationOut.model_validate(r) for r in rows]


@router.get("/investigations/{investigation_id}")
async def get_investigation(investigation: OwnedInvestigation) -> InvestigationOut:
    return InvestigationOut.model_validate(investigation)


async def events_after(session, investigation_id: uuid.UUID, after: int) -> list[dict]:
    rows = await session.scalars(
        select(InvestigationEvent)
        .where(
            InvestigationEvent.investigation_id == investigation_id,
            InvestigationEvent.seq > after,
        )
        .order_by(InvestigationEvent.seq)
    )
    return [
        {"seq": e.seq, "ts": e.ts.isoformat(), "type": e.type, "payload": e.payload} for e in rows
    ]


@router.get("/investigations/{investigation_id}/events")
async def list_events(
    investigation: OwnedInvestigation, session: SessionDep, after: int = Query(0, ge=0)
) -> list[dict]:
    """Saved events after `seq` (all by default): for replaying a finished investigation."""
    return await events_after(session, investigation.id, after)


def frame(event: dict) -> str:
    """One Server-Sent Events frame; `id` lets a reconnecting client send Last-Event-ID."""
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"


async def follow(investigation_id: uuid.UUID, after: int) -> AsyncIterator[str]:
    """Replay saved events, then poll for new ones until the run's `done` event."""
    last, idle = after, 0.0
    while True:
        async with SessionLocal() as session:  # short sessions: never hold one open for minutes
            events = await events_after(session, investigation_id, last)
            running = investigation_id in RUNNING
        for event in events:
            yield frame(event)
            last = event["seq"]
            if event["type"] == "done":
                return
        if not events and not running:
            return  # finished before we connected, or not started in this process
        idle = 0.0 if events else idle + POLL_S
        if idle >= KEEPALIVE_S:
            yield ": keep-alive\n\n"
            idle = 0.0
        await asyncio.sleep(POLL_S)


@router.get("/investigations/{investigation_id}/stream")
async def stream(
    investigation: OwnedInvestigation,
    last_event_id: Annotated[int | None, Header()] = None,
    after: int = Query(0, ge=0),
) -> StreamingResponse:
    return StreamingResponse(
        follow(investigation.id, last_event_id or after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/investigations/{investigation_id}/messages")
async def ask(
    body: MessageIn, investigation: OwnedInvestigation, session: SessionDep
) -> MessageOut:
    if investigation.report is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The investigation has no report yet")
    message = await followup.answer(session, get_router(), investigation.id, body.content)
    await session.commit()
    return MessageOut(id=message.id, role=message.role.value, content=message.content)


@router.post("/investigations/{investigation_id}/approve")
async def approve(investigation: OwnedInvestigation, session: SessionDep) -> PrOpenedOut:
    """The engineer's approval: open the verified fix as a draft pull request."""
    token = get_settings().github_token
    if token is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GITHUB_TOKEN is not configured")
    patch = await session.scalar(
        select(Patch)
        .where(Patch.investigation_id == investigation.id)
        .order_by(Patch.attempt.desc())
    )
    if patch is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This investigation has no fix to approve")
    try:
        url = await open_pr(session, patch.id, token.get_secret_value())
    except PrError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return PrOpenedOut(url=url)
