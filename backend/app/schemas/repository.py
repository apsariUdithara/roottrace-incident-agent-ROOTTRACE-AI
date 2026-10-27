"""Request and response bodies for /projects/{id}/repositories."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, SecretStr, StringConstraints

from app.models import IngestStatus, SandboxStatus

GitHubUrl = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, max_length=500, pattern=r"^https://github\.com/[\w.-]+/[\w.-]+$"
    ),
]
Branch = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Command = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
# Stripped, because a pasted token often ends with a newline.
Token = Annotated[
    SecretStr,
    BeforeValidator(lambda value: value.strip() if isinstance(value, str) else value),
    Field(min_length=1, max_length=255),
]


class RepositoryCreate(BaseModel):
    provider: Literal["github"] = "github"
    url: GitHubUrl  # e.g. https://github.com/acme/payments
    default_branch: Branch = "main"
    # Write-only: stored encrypted, never returned. Needed for private repos and draft PRs.
    token: Token | None = None
    setup_command: Command | None = None  # None: auto-detect (requirements.txt or pyproject)
    test_command: Command = "pytest -q"


class RepositoryUpdate(BaseModel):
    """Only the fields you send are changed. `setup_command: null` goes back to auto-detect,
    `token: null` removes the stored token, and null leaves the other fields unchanged."""

    default_branch: Branch | None = None
    token: Token | None = None  # replaces the stored token
    setup_command: Command | None = None
    test_command: Command | None = None


class RepositoryOut(BaseModel):
    id: uuid.UUID
    provider: str
    url: str
    default_branch: str
    has_token: bool  # the token itself is never returned
    setup_command: str | None
    test_command: str
    sandbox_status: SandboxStatus
    ingest_status: IngestStatus
    ingest_error: str | None  # why the last ingest failed
    last_indexed_sha: str | None
    last_indexed_at: datetime | None
    created_at: datetime
