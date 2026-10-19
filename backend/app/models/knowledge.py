"""What the agent searches: indexed documents and chunks, incidents, logs and long-term memory."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    EMPTY_JSON_OBJECT,
    Base,
    CreatedAtMixin,
    UUIDPrimaryKeyMixin,
    enum_check,
    enum_type,
)

# Must equal EMBED_DIMENSIONS: Qwen3-Embedding-8B is requested at 1024 dims so the HNSW index
# fits (pgvector indexes `vector` up to 2000 dims). Changing it needs a migration that alters
# both embedding columns and rebuilds their indexes.
EMBEDDING_DIMENSIONS = 1024


def hnsw_cosine_index(table: str) -> Index:
    return Index(
        f"ix_{table}_embedding",
        "embedding",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


class DocumentSourceType(StrEnum):
    DOC = "doc"
    CODE = "code"
    ISSUE = "issue"
    INCIDENT = "incident"


class Document(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("project_id", "source_type", "path"),
        enum_check("source_type", DocumentSourceType),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source_type: Mapped[DocumentSourceType] = mapped_column(enum_type(DocumentSourceType))
    path: Mapped[str] = mapped_column(String(1000))  # file path, or e.g. "issue:42", "INC-017"
    title: Mapped[str | None] = mapped_column(String(500))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))  # content hash, for incremental re-indexing


class DocumentChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),
        hnsw_cosine_index("document_chunks"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    section_path: Mapped[str | None] = mapped_column(String(1000))  # markdown heading path
    start_line: Mapped[int | None]
    end_line: Mapped[int | None]
    symbol: Mapped[str | None] = mapped_column(String(500))  # Python function or class
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Full-text vector maintained by Postgres; includes the symbol and heading path so
    # "connection pooling" or "calculate_total" match the right chunk.
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(symbol, '') || ' ' || coalesce(section_path, '')"
            " || ' ' || content)",
            persisted=True,
        ),
    )
    # "metadata" is reserved on declarative classes, so the attribute is `meta`.
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", default=dict, server_default=EMPTY_JSON_OBJECT
    )


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


class Incident(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("project_id", "key"),
        enum_check("status", IncidentStatus),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(50))  # e.g. INC-017
    title: Mapped[str] = mapped_column(String(500))
    severity: Mapped[str | None] = mapped_column(String(16))  # e.g. SEV2
    symptoms: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    fix: Mapped[str | None] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    status: Mapped[IncidentStatus] = mapped_column(
        enum_type(IncidentStatus),
        default=IncidentStatus.OPEN,
        server_default=IncidentStatus.OPEN.value,
    )
    occurred_at: Mapped[datetime | None]


class LogEntry(Base):
    """Structured logs, queried with SQL (never embedded). High volume, so a bigint key."""

    __tablename__ = "log_entries"
    __table_args__ = (
        Index("ix_log_entries_project_id_ts", "project_id", "ts"),
        Index("ix_log_entries_project_id_error_type", "project_id", "error_type"),
        Index("ix_log_entries_project_id_trace_id", "project_id", "trace_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    level: Mapped[str] = mapped_column(String(16))  # as logged: INFO, WARN, ERROR, ...
    service: Mapped[str | None] = mapped_column(String(200))
    instance: Mapped[str | None] = mapped_column(String(200))
    route: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[int | None]  # HTTP status
    latency_ms: Mapped[int | None]
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    error_type: Mapped[str | None] = mapped_column(String(200))
    trace_id: Mapped[str | None] = mapped_column(String(100))
    attrs: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=EMPTY_JSON_OBJECT)


class MemoryCategory(StrEnum):
    USER_PREF = "user_pref"
    PROJECT_FACT = "project_fact"
    ARCH_DECISION = "arch_decision"
    PREVIOUS_INCIDENT = "previous_incident"
    INVESTIGATION_CONTEXT = "investigation_context"


class MemoryAuthor(StrEnum):
    AGENT = "agent"
    USER = "user"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Memory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (
        enum_check("category", MemoryCategory),
        enum_check("created_by", MemoryAuthor),
        enum_check("status", MemoryStatus),
        hnsw_cosine_index("memories"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[MemoryCategory] = mapped_column(enum_type(MemoryCategory))
    content: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(1000))
    source_investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("investigations.id", ondelete="SET NULL")
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    created_by: Mapped[MemoryAuthor] = mapped_column(enum_type(MemoryAuthor))
    status: Mapped[MemoryStatus] = mapped_column(
        enum_type(MemoryStatus),
        default=MemoryStatus.ACTIVE,
        server_default=MemoryStatus.ACTIVE.value,
    )
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
