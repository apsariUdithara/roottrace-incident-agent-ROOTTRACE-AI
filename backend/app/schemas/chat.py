"""Request and response bodies for POST /chat."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.models import DocumentSourceType, ModelTier


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    """The conversation so far, oldest first; the last message is the new question."""

    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    # Ultra is never offered to chat: it is the expensive tier.
    tier: Literal["nano", "super"] = "nano"
    # Answer from this project's knowledge (docs, code, issues, incidents), citing it.
    project_id: uuid.UUID | None = None


class ChatCitation(BaseModel):
    """A source the answer cites as [S1], [S2], ..."""

    label: str  # "S1"
    source_type: DocumentSourceType
    path: str  # file path, or e.g. "issue:42", "INC-017"
    ref: str  # readable, e.g. "docs/pool.md § Pool L3-9 @ f89f6df"
    source_ref: str  # opens it in the source viewer, e.g. "doc:docs/pool.md"


class ChatResponse(BaseModel):
    content: str  # markdown; with a project, it cites sources as [S1]
    model: str  # the Nebius model ID that answered
    tier: ModelTier
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    cached: bool  # development only: a saved answer, at no cost
    citations: list[ChatCitation]  # the sources the answer cites, by label; empty without a project
