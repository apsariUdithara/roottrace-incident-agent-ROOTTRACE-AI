"""Bodies for the Knowledge page: what was indexed, uploads, logs, and the source viewer."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import DocumentSourceType
from app.schemas.common import Page, UtcDatetime
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


class UploadFormat(StrEnum):
    CSV = "csv"  # with a header row
    JSONL = "jsonl"  # one JSON object per line
    JSON = "json"  # a list of objects
    YAML = "yaml"  # a list of mappings


class UploadRowError(BaseModel):
    row: int  # 1-based, not counting a CSV header; a JSONL file's line number
    message: str


class UploadResult(BaseModel):
    kind: UploadKind
    filename: str
    imported: int
    skipped: int = Field(description="Rows already in the project, so not imported again")
    failed: int = Field(description="Rows with a problem, not imported")
    errors: list[UploadRowError] = Field(description="The first problems found, at most 20")


class UploadField(BaseModel):
    """One of our fields that a column of the file can fill."""

    name: str  # e.g. "ts"
    required: bool
    description: str


class UploadPreview(BaseModel):
    """What a file holds, and what importing it would do. Nothing is saved."""

    kind: UploadKind
    filename: str
    format: UploadFormat
    rows: int
    columns: list[str] = Field(description="In the order they first appear; nested keys as a.b")
    sample: list[dict[str, Any]] = Field(description="The first rows, by column")
    fields: list[UploadField]
    mapping: dict[str, str] = Field(
        description="Our field to the file's column: the one sent, or one guessed from the "
        "column names. Columns left out are kept with each log line (attrs)."
    )
    mapping_error: str | None = Field(description="Why the file can't be imported this way")
    result: UploadResult | None = Field(description="What importing would do; null if it can't")


# --- Logs --------------------------------------------------------------------------------------


class LogQuery(BaseModel):
    """Filters for GET /projects/{id}/logs. All are optional and combine with AND.

    They match as in the agent's search_logs tool, so the same filters find the same lines.
    """

    level: str | None = Field(default=None, max_length=16, description="e.g. ERROR, any case")
    service: str | None = Field(default=None, max_length=200)
    route: str | None = Field(default=None, max_length=500, description="The route or part of it")
    error_type: str | None = Field(default=None, max_length=200, description="e.g. SlowQuery")
    text: str | None = Field(
        default=None, max_length=200, description="In the message or error type, any case"
    )
    trace_id: str | None = Field(default=None, max_length=100)
    since: UtcDatetime | None = Field(default=None, description="Inclusive; UTC if no timezone")
    until: UtcDatetime | None = Field(default=None, description="Inclusive; UTC if no timezone")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.since and self.until and self.since > self.until:
            raise ValueError("since must not be after until")
        return self


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


class LogValue(BaseModel):
    value: str
    count: int  # lines with this value


class LogFacets(BaseModel):
    """What the project's logs hold: the values to filter by, and the time they cover."""

    total: int
    first_ts: datetime | None  # None when there are no logs
    last_ts: datetime | None
    # Each list is the most frequent values (at most 100), in alphabetical order.
    levels: list[LogValue]
    services: list[LogValue]
    routes: list[LogValue]
    error_types: list[LogValue]


# --- Evidence sources --------------------------------------------------------------------------
# GET /projects/{id}/sources?ref= resolves a `source_ref` to something the viewer can show.
# The prefix picks the kind; the parts in brackets are optional:
#
#   file:<path>[#L<start>-L<end>]@<sha>   commit:<sha>        doc:<path>[#<section-slug>]
#   log:<trace_id>                        incident:<key>      issue:<number>      memory:<id>
#
# Evidence from the `database` source type has no ref to resolve: the viewer shows its excerpt.


def source_ref(
    source_type: DocumentSourceType,
    path: str,
    commit_sha: str | None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """The ref that opens an indexed document (or some of its lines) in the source viewer."""
    match source_type:
        case DocumentSourceType.DOC:
            return f"doc:{path}"
        case DocumentSourceType.CODE:
            lines = f"#L{start_line}-L{end_line}" if start_line and end_line else ""
            return f"file:{path}{lines}@{commit_sha or 'HEAD'}"
        case DocumentSourceType.INCIDENT:
            return f"incident:{path}"
        case DocumentSourceType.ISSUE:
            return path  # already "issue:<number>"


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
    trace_id: str | None  # None for a deploy line logged without one
    entries: list[LogEntryOut]  # every line with this trace_id (or the deploy's), oldest first


class IncidentSource(_Source):
    kind: Literal["incident"]
    incident: IncidentOut


class IssueSource(_Source):
    kind: Literal["issue"]
    number: int
    title: str
    body: str  # markdown
    # The index keeps only an issue's number, state, title and body, so no labels; and the state
    # and a link only when they're known.
    state: Literal["open", "closed"] | None
    url: str | None


class MemorySource(_Source):
    kind: Literal["memory"]
    memory: MemoryOut


Source = Annotated[
    FileSource | CommitSource | DocSource | LogSource | IncidentSource | IssueSource | MemorySource,
    Field(discriminator="kind"),
]
