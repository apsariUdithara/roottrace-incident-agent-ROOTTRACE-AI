"""Bodies for the Knowledge page: what was indexed, uploads, logs, and the source viewer."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import DocumentSourceType
from app.schemas.common import Page
from app.schemas.incident import IncidentOut
from app.schemas.memory import MemoryOut

# --- Indexed items -----------------------------------------------------------------------------


class KnowledgeItem(BaseModel):
    """One indexed document: a doc file, a code file, an issue or an incident."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: DocumentSourceType
    path: str  # file path, or e.g. "issue:42", "INC-017"
    title: str | None
    commit_sha: str | None
    chunk_count: int
    source_ref: str = Field(description='Opens the item in the source viewer, e.g. "doc:README.md"')
    created_at: datetime


class KnowledgePage(Page[KnowledgeItem]):
    pass


# --- Uploads -----------------------------------------------------------------------------------


class UploadKind(StrEnum):
    LOGS = "logs"  # JSONL or CSV
    INCIDENTS = "incidents"  # YAML, JSON or CSV


class UploadRowError(BaseModel):
    row: int  # 1-based, not counting a CSV header
    message: str


class UploadResult(BaseModel):
    kind: UploadKind
    filename: str
    imported: int
    skipped: int
    errors: list[UploadRowError] = Field(description="The first problems found, at most 20")


# --- Logs --------------------------------------------------------------------------------------


class LogQuery(BaseModel):
    """Filters for GET /projects/{id}/logs. All are optional and combine with AND."""

    level: str | None = Field(default=None, max_length=16)  # as logged, e.g. ERROR
    service: str | None = Field(default=None, max_length=200)
    route: str | None = Field(default=None, max_length=500)
    error_type: str | None = Field(default=None, max_length=200)
    trace_id: str | None = Field(default=None, max_length=100)
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class LogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    level: str
    service: str | None
    instance: str | None
    route: str | None
    status: int | None  # HTTP status
    latency_ms: int | None
    message: str
    error_type: str | None
    trace_id: str | None
    attrs: dict[str, Any]


class LogPage(Page[LogEntryOut]):
    pass


# --- Evidence sources --------------------------------------------------------------------------
# GET /projects/{id}/sources?ref= resolves a `source_ref` to something the viewer can show.
# The prefix picks the kind; the parts in brackets are optional:
#
#   file:<path>[#L<start>-L<end>]@<sha>   commit:<sha>        doc:<path>[#<section-slug>]
#   log:<trace_id>                        incident:<key>      issue:<number>      memory:<id>
#
# Evidence from the `database` source type has no ref to resolve: the viewer shows its excerpt.


class _Source(BaseModel):
    ref: str  # the source_ref that was resolved


class FileSource(_Source):
    kind: Literal["file"]
    path: str
    sha: str
    language: str | None  # for syntax highlighting, e.g. "python"
    content: str  # the whole file at `sha`
    start_line: int | None  # lines to highlight, 1-based and inclusive
    end_line: int | None


class CommitSource(_Source):
    kind: Literal["commit"]
    sha: str
    message: str
    author: str
    committed_at: datetime
    files: list[str]
    diff: str  # unified diff


class DocSource(_Source):
    kind: Literal["doc"]
    path: str
    section: str | None  # heading slug; None for the whole document
    title: str | None
    markdown: str


class LogSource(_Source):
    kind: Literal["log"]
    trace_id: str
    entries: list[LogEntryOut]  # every line with this trace_id, oldest first


class IncidentSource(_Source):
    kind: Literal["incident"]
    incident: IncidentOut


class IssueSource(_Source):
    kind: Literal["issue"]
    number: int
    title: str
    body: str  # markdown
    state: Literal["open", "closed"]
    labels: list[str]
    url: str


class MemorySource(_Source):
    kind: Literal["memory"]
    memory: MemoryOut


Source = Annotated[
    FileSource | CommitSource | DocSource | LogSource | IncidentSource | IssueSource | MemorySource,
    Field(discriminator="kind"),
]
