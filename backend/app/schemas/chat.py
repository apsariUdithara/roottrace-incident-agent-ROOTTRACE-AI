"""Request and response bodies for POST /chat."""

from typing import Literal

from pydantic import BaseModel, Field

from app.models import ModelTier


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    """The conversation so far, oldest first; the last message is the new question."""

    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    # Ultra is never offered to chat: it is the expensive tier.
    tier: Literal["nano", "super"] = "nano"


class ChatResponse(BaseModel):
    content: str  # markdown
    model: str  # the Nebius model ID that answered
    tier: ModelTier
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    cached: bool  # development only: a saved answer, at no cost
