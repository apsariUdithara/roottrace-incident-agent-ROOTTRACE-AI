"""All tables. Import every model here so Alembic sees it in Base.metadata."""

from app.models.base import Base
from app.models.fix import Patch, PatchStatus, TestPhase, TestRun
from app.models.investigation import (
    Evidence,
    EvidenceKind,
    EvidenceSourceType,
    Hypothesis,
    Investigation,
    InvestigationEvent,
    InvestigationStatus,
    Message,
    MessageRole,
    Stance,
    Verdict,
)
from app.models.knowledge import (
    EMBEDDING_DIMENSIONS,
    Document,
    DocumentChunk,
    DocumentSourceType,
    Incident,
    IncidentStatus,
    LogEntry,
    Memory,
    MemoryAuthor,
    MemoryCategory,
    MemoryStatus,
)
from app.models.observability import EvaluationRun, ModelTier, SpanKind, SpanStatus, TraceSpan
from app.models.project import Project, Repository, SandboxStatus
from app.models.user import User

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "Base",
    "Document",
    "DocumentChunk",
    "DocumentSourceType",
    "EvaluationRun",
    "Evidence",
    "EvidenceKind",
    "EvidenceSourceType",
    "Hypothesis",
    "Incident",
    "IncidentStatus",
    "Investigation",
    "InvestigationEvent",
    "InvestigationStatus",
    "LogEntry",
    "Memory",
    "MemoryAuthor",
    "MemoryCategory",
    "MemoryStatus",
    "Message",
    "MessageRole",
    "ModelTier",
    "Patch",
    "PatchStatus",
    "Project",
    "Repository",
    "SandboxStatus",
    "SpanKind",
    "SpanStatus",
    "Stance",
    "TestPhase",
    "TestRun",
    "TraceSpan",
    "User",
    "Verdict",
]
