"""POST /chat: a plain conversation with Nemotron through the model router (Phase 2)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.llm.router import LLMRouter, get_router

router = APIRouter()


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=20)
    # Ultra is never offered to chat: it is the expensive tier.
    tier: Literal["nano", "super"] = "nano"


class ChatResponse(BaseModel):
    content: str
    model: str
    tier: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    cached: bool


@router.post("/chat")
async def chat(
    req: ChatRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    llm: Annotated[LLMRouter, Depends(get_router)],
) -> ChatResponse:
    # ponytail: no login yet, so refuse in production rather than let anyone spend our credits.
    # Remove once /chat requires an authenticated user.
    if settings.app_env == "production":
        raise HTTPException(
            status_code=403, detail="chat is disabled until authentication is in place"
        )
    result = await llm.complete(
        req.tier, [m.model_dump() for m in req.messages], max_tokens=512, name="chat"
    )
    return ChatResponse(
        content=result.content or "",
        **{k: getattr(result, k) for k in ChatResponse.model_fields if k != "content"},
    )
