"""The event bus: every agent event is numbered, stored and handed to live listeners.

The agent only builds payloads (schemas/events.py). `publish` wraps each in its envelope,
saves it to `investigation_events` (so a reload or reconnect replays everything) and passes it
to any listener: the SSE stream, or a terminal printer.
"""

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InvestigationEvent
from app.schemas.events import EventPayload, make_event

Listener = Callable[[object], Awaitable[None] | None]


class EventBus:
    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID):
        self.session = session
        self.investigation_id = investigation_id
        self.seq = 0
        self.listeners: list[Listener] = []

    async def publish(self, payload: EventPayload):
        self.seq += 1
        event = make_event(self.seq, payload)
        self.session.add(
            InvestigationEvent(
                investigation_id=self.investigation_id,
                seq=event.seq,
                type=event.type,
                payload=payload.model_dump(mode="json"),
                ts=event.ts,
            )
        )
        await self.session.commit()  # committed per event, so other readers see it live
        for listener in self.listeners:
            result = listener(event)
            if result is not None:
                await result
        return event
