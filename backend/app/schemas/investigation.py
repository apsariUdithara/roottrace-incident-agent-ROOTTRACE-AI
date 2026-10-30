"""Request and response bodies for the investigation routes.

Live progress is the event stream (schemas/events.py): evidence, hypotheses, patches and test
runs reach the page as events, from `/stream` while a run is going and from `/events` after.
These bodies are the investigation row itself, the follow-up chat, approval and the trace.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import InvestigationStatus, MessageRole, ModelTier, SpanKind, SpanStatus
from app.schemas.events import ReportReady


class InvestigationCreate(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    ref: str | None = Field(
        default=None, max_length=200, description="Deployed version; default: the default branch"
    )
    reported_at: datetime | None = None


class InvestigationReport(ReportReady):
    """What `investigations.report` stores: the `report` event's payload, plus the version that
    was investigated (a PR for the fix is opened against it)."""

    # None in reports saved before it was recorded: those must still open (no PR can be made).
    deployed_ref: str | None = None


class InvestigationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    question: str
    status: InvestigationStatus
    root_cause: str | None
    confidence: float | None
    report: InvestigationReport | None  # None until the run has written its report
    started_at: datetime
    finished_at: datetime | None
    total_tokens: int
    total_cost_usd: float


class MessageIn(BaseModel):
    content: str = Field(min_length=2, max_length=2000)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str  # markdown; cites evidence by label, e.g. "[F2]"


class PrOpenedOut(BaseModel):
    url: str


# --- Trace -------------------------------------------------------------------------------------


class TraceSpanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None  # nest spans by this for the waterfall
    kind: SpanKind
    name: str
    model: str | None  # the Nebius model ID, for LLM spans
    tier: ModelTier | None
    prompt_version: str | None
    started_at: datetime
    latency_ms: int | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cached: bool
    downgraded: bool  # Ultra was rate-limited, so Super answered
    status: SpanStatus
    error: str | None
    input: dict[str, Any] | None
    output: dict[str, Any] | None


class TraceTotals(BaseModel):
    spans: int
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    duration_ms: int  # first span's start to the last span's end


class Trace(BaseModel):
    spans: list[TraceSpanOut]  # in start order
    totals: TraceTotals
