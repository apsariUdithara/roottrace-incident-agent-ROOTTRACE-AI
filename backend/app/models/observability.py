"""Traces for every LLM, tool, retrieval, MCP and sandbox call, and evaluation runs."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    EMPTY_JSON_ARRAY,
    EMPTY_JSON_OBJECT,
    Base,
    UUIDPrimaryKeyMixin,
    enum_check,
    enum_type,
)


class SpanKind(StrEnum):
    LLM = "llm"
    TOOL = "tool"
    MCP = "mcp"
    RETRIEVAL = "retrieval"
    MEMORY = "memory"
    SANDBOX = "sandbox"


class ModelTier(StrEnum):
    ULTRA = "ultra"
    SUPER = "super"
    NANO = "nano"


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class TraceSpan(UUIDPrimaryKeyMixin, Base):
    """One timed call. Ids are generated in Python, so a parent's id is known before insert."""

    __tablename__ = "trace_spans"
    __table_args__ = (
        enum_check("kind", SpanKind),
        enum_check("tier", ModelTier),
        enum_check("status", SpanStatus),
    )

    # NULL for calls outside an investigation, e.g. /chat or ingestion.
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trace_spans.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[SpanKind] = mapped_column(enum_type(SpanKind))
    name: Mapped[str] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    tier: Mapped[ModelTier | None] = mapped_column(enum_type(ModelTier))
    prompt_version: Mapped[str | None] = mapped_column(String(50))  # e.g. planner.v1
    input: Mapped[dict[str, Any] | None]
    output: Mapped[dict[str, Any] | None]
    prompt_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal(0), server_default="0"
    )
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())  # waterfall offset
    latency_ms: Mapped[int | None]
    cached: Mapped[bool] = mapped_column(default=False, server_default="false")
    downgraded: Mapped[bool] = mapped_column(default=False, server_default="false")  # Ultra→Super
    status: Mapped[SpanStatus] = mapped_column(
        enum_type(SpanStatus), default=SpanStatus.OK, server_default=SpanStatus.OK.value
    )
    error: Mapped[str | None] = mapped_column(Text)


class EvaluationRun(UUIDPrimaryKeyMixin, Base):
    """One `python eval/evaluate.py` run. Not project-scoped."""

    __tablename__ = "evaluation_runs"

    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    git_sha: Mapped[str | None] = mapped_column(String(64))
    model_config: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=EMPTY_JSON_OBJECT
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=EMPTY_JSON_OBJECT)
    # Per-case results, failures included.
    cases: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=EMPTY_JSON_ARRAY
    )
