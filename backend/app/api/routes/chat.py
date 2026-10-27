"""POST /chat: a plain conversation with Nemotron through the model router.

It needs a login, and each user may send CHAT_MESSAGES_PER_HOUR messages, so nobody can spend
the public demo's credits from here.
"""

import logging
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated

import openai
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser
from app.core.config import Settings, get_settings
from app.llm.router import LLMRouter, get_router
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=1)
# Kept in this process, like the background investigation runs: right for the single demo
# server. A restart forgets the counts, which only ever errs towards letting someone chat.
recent_messages: defaultdict[uuid.UUID, deque[datetime]] = defaultdict(deque)


def check_limit(user_id: uuid.UUID, per_hour: int) -> None:
    now = datetime.now(UTC)
    recent = recent_messages[user_id]
    while recent and recent[0] <= now - WINDOW:
        recent.popleft()
    if len(recent) >= per_hour:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, f"Limit reached: {per_hour} chat messages per hour."
        )
    recent.append(now)


@router.post("/chat")
async def chat(
    req: ChatRequest,
    user: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
    llm: Annotated[LLMRouter, Depends(get_router)],
) -> ChatResponse:
    check_limit(user.id, settings.chat_messages_per_hour)
    try:
        result = await llm.complete(
            req.tier, [m.model_dump() for m in req.messages], max_tokens=512, name="chat"
        )
    except (openai.APIError, TimeoutError) as exc:  # still failing after the router's retries
        # The user sees a plain message; the server log keeps the reason (an expired key, an
        # outage), never the conversation.
        logger.warning("chat: the model provider failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The model didn't answer. Please try again in a moment."
        ) from exc
    return ChatResponse(
        content=result.content or "",
        **{k: getattr(result, k) for k in ChatResponse.model_fields if k != "content"},
    )
