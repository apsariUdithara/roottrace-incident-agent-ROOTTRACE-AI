"""Request and response bodies for /projects/{id}/incidents."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import IncidentStatus

IncidentKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Severity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20_000)]
CommitSha = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{7,64}$")]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class IncidentCreate(BaseModel):
    key: IncidentKey  # e.g. INC-017; unique within the project
    title: Title
    severity: Severity | None = None  # e.g. SEV2
    symptoms: LongText | None = None
    root_cause: LongText | None = None
    fix: LongText | None = None
    commit_sha: CommitSha | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    status: IncidentStatus = IncidentStatus.OPEN
    occurred_at: datetime | None = None


class IncidentUpdate(BaseModel):
    """Only the fields you send are changed."""

    key: IncidentKey | None = None
    title: Title | None = None
    severity: Severity | None = None
    symptoms: LongText | None = None
    root_cause: LongText | None = None
    fix: LongText | None = None
    commit_sha: CommitSha | None = None
    tags: list[Tag] | None = Field(default=None, max_length=20)
    status: IncidentStatus | None = None
    occurred_at: datetime | None = None


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    title: str
    severity: str | None
    symptoms: str | None
    root_cause: str | None
    fix: str | None
    commit_sha: str | None
    tags: list[str]
    status: IncidentStatus
    occurred_at: datetime | None
    created_at: datetime
