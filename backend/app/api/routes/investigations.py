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
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.agent import followup, run
from app.agent.pr import PrError, open_pr
from app.api.deps import CurrentUser, OwnedInvestigation, OwnedProject
from app.core.config import get_settings
from app.core.db import SessionDep, SessionLocal
from app.core.errors import not_implemented
from app.llm.router import get_router
from app.models import (
    Investigation,
    InvestigationEvent,
    InvestigationStatus,
    Message,
    Patch,
    Repository,
)
from app.schemas.events import EVENT_CLASSES, Event
from app.schemas.investigation import (
    InvestigationCreate,
    InvestigationOut,
    MessageIn,
    MessageOut,
    PrOpenedOut,
    Trace,
)

router = APIRouter(tags=["investigations"])

POLL_S = 0.5  # how often the stream checks for new events
KEEPALIVE_S = 15  # an SSE comment this often keeps proxies from closing an idle stream
RUNNING: dict[uuid.UUID, asyncio.Task] = {}  # background runs; kept so they aren't GC'd

# OpenAPI for /stream. A StreamingResponse has no schema of its own, so this documents each
# frame's `data:` as one event, pointing at the same schemas `/events` declares.
STREAM_RESPONSE = {
    "description": "Server-Sent Events: `id: <seq>`, `event: <type>`, `data: <event JSON>`",
    "content": {
        "text/event-stream": {
            "itemSchema": {
                "type": "object",
                "required": ["data"],
                "properties": {
                    "id": {"type": "string"},
                    "event": {"type": "string"},
                    "data": {
                        "type": "string",
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "oneOf": [
                                {"$ref": f"#/components/schemas/{cls.__name__}"}
                                for cls in EVENT_CLASSES
                            ],
                            "discriminator": {"propertyName": "type"},
                        },
                    },
                },
            }
        }
    },
}


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
    # Indexed at least once: the agent reads the clone, which indexing makes.
    has_repo = await session.scalar(
        select(Repository.id).where(
            Repository.project_id == project.id, Repository.local_path.is_not(None)
        )
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
) -> list[Event]:
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


@router.get(
    "/investigations/{investigation_id}/stream",
    response_class=StreamingResponse,
    responses={200: STREAM_RESPONSE},
)
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


@router.get("/investigations/{investigation_id}/messages")
async def list_messages(
    investigation: OwnedInvestigation, session: SessionDep
) -> list[MessageOut]:
    """The follow-up chat, oldest first."""
    rows = await session.scalars(
        select(Message)
        .where(Message.investigation_id == investigation.id)
        .order_by(Message.created_at, Message.id)
    )
    return [MessageOut(id=m.id, role=m.role.value, content=m.content) for m in rows]


@router.post(
    "/investigations/{investigation_id}/patches/{patch_id}/rerun",
    status_code=status.HTTP_202_ACCEPTED,
)
async def rerun_patch(patch_id: uuid.UUID, investigation: OwnedInvestigation) -> InvestigationOut:
    """Runs the baseline and patched tests again; the output streams as `test_output` events."""
    raise not_implemented("B-604")


@router.get("/investigations/{investigation_id}/trace")
async def get_trace(investigation: OwnedInvestigation) -> Trace:
    """Every LLM, tool, retrieval, memory, MCP and sandbox call in the run, with totals."""
    raise not_implemented("B-803")
