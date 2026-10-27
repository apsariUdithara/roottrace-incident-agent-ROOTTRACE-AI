"""What the agent knows about a project: indexing, uploads, browsing, logs and evidence sources."""

from typing import Annotated

from fastapi import APIRouter, Form, Query, UploadFile, status
from pydantic import Json

from app.api.deps import OwnedProject
from app.core.errors import not_implemented
from app.models import DocumentSourceType
from app.schemas.knowledge import (
    KnowledgePage,
    LogPage,
    LogQuery,
    Source,
    UploadKind,
    UploadResult,
)
from app.schemas.repository import RepositoryOut

router = APIRouter(prefix="/projects/{project_id}", tags=["knowledge"])


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_project(project: OwnedProject) -> list[RepositoryOut]:
    """Re-indexes the project in the background. Poll the repositories' `ingest_status`."""
    raise not_implemented("B-304")


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_file(
    project: OwnedProject,
    kind: Annotated[UploadKind, Form()],
    file: UploadFile,
    mapping: Annotated[
        Json[dict[str, str]] | None,
        Form(description='JSON: our field to your column, e.g. {"ts": "timestamp"}'),
    ] = None,
) -> UploadResult:
    """Imports logs (JSONL or CSV) or incidents (YAML, JSON or CSV)."""
    raise not_implemented("B-310")


@router.get("/knowledge")
async def list_knowledge(
    project: OwnedProject,
    type_: Annotated[DocumentSourceType | None, Query(alias="type")] = None,
    q: Annotated[str | None, Query(max_length=200, description="Matches title or path")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> KnowledgePage:
    raise not_implemented("B-304")


@router.get("/sources")
async def resolve_source(
    project: OwnedProject,
    ref: Annotated[str, Query(min_length=3, max_length=1000, examples=["commit:a3f9c21"])],
) -> Source:
    """Resolves an evidence `source_ref` for the source viewer. 404 if it doesn't resolve."""
    raise not_implemented("B-401")


@router.get("/logs")
async def list_logs(project: OwnedProject, query: Annotated[LogQuery, Query()]) -> LogPage:
    """Newest first."""
    raise not_implemented("B-309")
