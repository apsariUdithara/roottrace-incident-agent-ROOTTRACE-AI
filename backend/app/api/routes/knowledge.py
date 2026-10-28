"""What the agent knows about a project: indexing, uploads, browsing, logs and evidence sources."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from pydantic import Json
from sqlalchemy import func, or_, select

from app.api.deps import OwnedProject
from app.core.db import SessionDep
from app.core.errors import not_implemented
from app.llm.router import LLMRouter, get_router
from app.models import Document, DocumentChunk, DocumentSourceType, IngestStatus, Repository
from app.rag import indexing
from app.schemas.knowledge import (
    KnowledgeItem,
    KnowledgePage,
    LogPage,
    LogQuery,
    Source,
    UploadKind,
    UploadResult,
    source_ref,
)
from app.schemas.repository import RepositoryOut

router = APIRouter(prefix="/projects/{project_id}", tags=["knowledge"])
RouterDep = Annotated[LLMRouter, Depends(get_router)]


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_project(
    project: OwnedProject, session: SessionDep, llm: RouterDep
) -> list[RepositoryOut]:
    """Re-indexes the project's repository in the background: its files at the default branch.
    Poll GET /projects/{id}/repositories until `ingest_status` is `indexed` or `failed`.

    A run that's already going isn't started again. 409 if there's no repository.
    """
    repositories = list(
        await session.scalars(
            select(Repository)
            .where(Repository.project_id == project.id)
            .order_by(Repository.created_at)
        )
    )
    if not repositories:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect a repository to this project first")
    starting = [repository for repository in repositories if indexing.claim(repository.id)]
    try:
        for repository in starting:
            repository.ingest_status = IngestStatus.QUEUED
            repository.ingest_error = None
        await session.commit()
    except BaseException:
        for repository in starting:
            indexing.release(repository.id)
        raise
    for repository in starting:
        indexing.start(repository.id, llm)
    return [RepositoryOut.model_validate(repository) for repository in repositories]


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
    *,
    session: SessionDep,
) -> KnowledgePage:
    """What the agent can search: the indexed documents, by type and then path (ignoring case)."""
    filters = [Document.project_id == project.id]
    if type_:
        filters.append(Document.source_type == type_)
    if q:
        filters.append(
            or_(
                Document.title.icontains(q, autoescape=True),
                Document.path.icontains(q, autoescape=True),
            )
        )
    chunks = (
        select(func.count())
        .where(DocumentChunk.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    total = await session.scalar(select(func.count()).select_from(Document).where(*filters))
    rows = await session.execute(
        select(Document, chunks)
        .where(*filters)
        .order_by(Document.source_type, func.lower(Document.path).collate("C"), Document.path)
        .limit(limit)
        .offset(offset)
    )
    items = [
        KnowledgeItem(
            id=document.id,
            source_type=document.source_type,
            path=document.path,
            title=document.title,
            commit_sha=document.commit_sha,
            chunk_count=chunk_count,
            source_ref=source_ref(document.source_type, document.path, document.commit_sha),
            created_at=document.created_at,
        )
        for document, chunk_count in rows
    ]
    return KnowledgePage(items=items, total=total, limit=limit, offset=offset)


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
