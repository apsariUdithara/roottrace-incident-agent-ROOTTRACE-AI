"""One investigation: its run state, chat, streamed events, hypotheses and evidence."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    EMPTY_JSON_OBJECT,
    Base,
    CreatedAtMixin,
    UUIDPrimaryKeyMixin,
    enum_check,
    enum_type,
)


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"  # paused at interrupt() before opening a PR
    COMPLETED = "completed"
    FAILED = "failed"


class Investigation(UUIDPrimaryKeyMixin, Base):
    """The LangGraph thread_id is this row's id."""

    __tablename__ = "investigations"
    __table_args__ = (
        Index("ix_investigations_project_id_started_at", "project_id", "started_at"),
        enum_check("status", InvestigationStatus),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[InvestigationStatus] = mapped_column(
        enum_type(InvestigationStatus),
        default=InvestigationStatus.QUEUED,
        server_default=InvestigationStatus.QUEUED.value,
    )
    root_cause: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None]  # 0..1, computed by agent/confidence.py
    confidence_breakdown: Mapped[dict[str, Any] | None]
    report: Mapped[dict[str, Any] | None]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    total_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal(0), server_default="0"
    )


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Message(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Follow-up chat on an investigation."""

    __tablename__ = "messages"
    __table_args__ = (enum_check("role", MessageRole),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(enum_type(MessageRole))
    content: Mapped[str] = mapped_column(Text)


class InvestigationEvent(Base):
    """The SSE log: every streamed event, so a reload or reconnect replays from Last-Event-ID.

    `type` is free text here; the event schema (schemas/events.py) validates it.
    """

    __tablename__ = "investigation_events"
    __table_args__ = (UniqueConstraint("investigation_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE")
    )
    seq: Mapped[int]  # per-investigation order; also the SSE `id:`
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=EMPTY_JSON_OBJECT)
    ts: Mapped[datetime] = mapped_column(server_default=func.now())


class Verdict(StrEnum):
    SUPPORTED = "supported"
    WEAK = "weak"
    REFUTED = "refuted"


class Hypothesis(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "hypotheses"
    __table_args__ = (enum_check("verdict", Verdict),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    statement: Mapped[str] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(default=1, server_default="1")
    verdict: Mapped[Verdict | None] = mapped_column(enum_type(Verdict))  # NULL until verified
    verifier_notes: Mapped[str | None] = mapped_column(Text)


class EvidenceKind(StrEnum):
    FACT = "fact"  # observed by a tool; must be cited
    INFERENCE = "inference"  # reasoned; cites facts through `cites`


class EvidenceSourceType(StrEnum):
    CODE = "code"
    COMMIT = "commit"
    DOC = "doc"
    LOG = "log"
    INCIDENT = "incident"
    ISSUE = "issue"
    MEMORY = "memory"
    DATABASE = "database"


class Stance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"


class Evidence(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        enum_check("kind", EvidenceKind),
        enum_check("source_type", EvidenceSourceType),
        enum_check("stance", Stance),
        # The product's core rule: a fact always carries its source and a verbatim excerpt.
        CheckConstraint(
            "kind <> 'fact' OR "
            "(source_type IS NOT NULL AND source_ref IS NOT NULL AND excerpt IS NOT NULL)",
            name="fact_is_cited",
        ),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[EvidenceKind] = mapped_column(enum_type(EvidenceKind))
    statement: Mapped[str] = mapped_column(Text)  # the claim shown in the UI
    tool: Mapped[str | None] = mapped_column(String(100))  # tool that observed a fact
    source_type: Mapped[EvidenceSourceType | None] = mapped_column(enum_type(EvidenceSourceType))
    source_ref: Mapped[str | None] = mapped_column(String(1000))  # e.g. commit:a3f9c21
    excerpt: Mapped[str | None] = mapped_column(Text)
    cites: Mapped[list[uuid.UUID]] = mapped_column(  # facts an inference is based on
        ARRAY(Uuid), default=list, server_default="{}"
    )
    supports_hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="SET NULL")
    )
    stance: Mapped[Stance] = mapped_column(
        enum_type(Stance), default=Stance.NEUTRAL, server_default=Stance.NEUTRAL.value
    )
    weight: Mapped[float] = mapped_column(default=1.0, server_default="1")
