"""What the agent knows about a project: indexing, uploads, browsing, logs and evidence sources."""

import logging
from collections.abc import Callable
from pathlib import PurePath
from typing import Annotated

import openai
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import Json
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import OwnedProject
from app.core.db import SessionDep
from app.core.errors import not_implemented
from app.llm.router import LLMRouter, get_router
from app.models import (
    Document,
    DocumentChunk,
    DocumentSourceType,
    IngestStatus,
    LogEntry,
    Project,
    Repository,
)
from app.rag import indexing, uploads
from app.schemas.knowledge import (
    KnowledgeItem,
    KnowledgePage,
    LogEntryOut,
    LogFacets,
    LogPage,
    LogQuery,
    LogValue,
    Source,
    UploadField,
    UploadKind,
    UploadPreview,
    UploadResult,
    source_ref,
)
from app.schemas.repository import RepositoryOut
from app.tools.logs import LogSearchArgs, log_filters

router = APIRouter(prefix="/projects/{project_id}", tags=["knowledge"])
logger = logging.getLogger(__name__)
RouterDep = Annotated[LLMRouter, Depends(get_router)]


def router_when_needed(request: Request) -> Callable[[], LLMRouter]:
    """get_router, to call only if the model is needed: making it needs a Nebius key, which
    uploading logs doesn't. Tests' override of get_router still applies."""
    return request.app.dependency_overrides.get(get_router, get_router)


FACET_SIZE = 100  # values per field: the most frequent


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


KindForm = Annotated[UploadKind, Form()]
MappingForm = Annotated[
    Json[dict[str, str]] | None,
    Form(
        description='JSON: our field to your column, e.g. {"ts": "timestamp"}. Leave it out to '
        "use the mapping guessed from the column names (see /uploads/preview)."
    ),
]


async def _plan(
    session: SessionDep,
    project: OwnedProject,
    kind: UploadKind,
    file: UploadFile,
    mapping: dict[str, str] | None,
) -> tuple[str, uploads.Plan]:
    """The file's name and what importing it would do. 413 if it's too big, 422 if it can't be
    read."""
    filename = PurePath(file.filename or "upload").name[:255]
    data = await file.read(uploads.MAX_BYTES + 1)
    if len(data) > uploads.MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"The file is larger than {uploads.MAX_BYTES // 2**20} MB. Split it, and upload "
            "the parts one by one.",
        )
    try:
        return filename, await uploads.plan(session, project.id, kind, filename, data, mapping)
    except uploads.UploadError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.post("/uploads/preview")
async def preview_upload(
    project: OwnedProject,
    kind: KindForm,
    file: UploadFile,
    mapping: MappingForm = None,
    *,
    session: SessionDep,
) -> UploadPreview:
    """What uploading the file would do, without saving anything: its columns and first rows,
    the mapping (the one sent, or one guessed from the column names) and the rows that would be
    imported, skipped or refused. Send it again with another mapping to see the difference."""
    filename, found = await _plan(session, project, kind, file, mapping)
    return UploadPreview(
        kind=kind,
        filename=filename,
        format=found.format,
        rows=found.rows,
        columns=found.columns,
        sample=found.sample,
        fields=[
            UploadField(name=t.name, required=t.required, description=t.description)
            for t in uploads.FIELDS[kind]
        ],
        mapping=found.mapping,
        mapping_error=found.mapping_error,
        result=found.result,
    )


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_file(
    project: OwnedProject,
    kind: KindForm,
    file: UploadFile,
    mapping: MappingForm = None,
    *,
    session: SessionDep,
    llm: Annotated[Callable[[], LLMRouter], Depends(router_when_needed)],
) -> UploadResult:
    """Imports logs (JSONL or CSV) or incidents (YAML, JSON or CSV), as the preview showed.

    Rows with a problem are left out and listed; rows already in the project are skipped, and an
    incident is never overwritten. Uploaded incidents are embedded for the agent's search: if
    that fails, nothing is saved (502). 422 if the mapping leaves out a required field.
    """
    # Locks the project row: two uploads at once could otherwise both add the same lines.
    await session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    filename, found = await _plan(session, project, kind, file, mapping)
    if found.mapping_error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, found.mapping_error)
    try:
        await uploads.save(session, llm, project.id, kind, found)  # calls llm() for incidents
        await session.commit()
    except (openai.APIError, TimeoutError) as exc:
        logger.warning("uploads: indexing %s failed: %s: %s", filename, type(exc).__name__, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't index the incidents for search, so none were saved. "
            "Please try again in a moment.",
        ) from exc
    except IntegrityError as exc:  # an incident with one of the keys was added meanwhile
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An incident in the file was added to the project meanwhile. Upload it again.",
        ) from exc
    assert found.result is not None
    return found.result


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
async def list_logs(
    project: OwnedProject, query: Annotated[LogQuery, Query()], session: SessionDep
) -> LogPage:
    """The project's log lines, newest first. The filters match as in the agent's search_logs
    tool, so its evidence can be checked here."""
    search = LogSearchArgs.model_validate(query.model_dump(include=set(LogSearchArgs.model_fields)))
    filters = log_filters(project.id, search)
    if query.trace_id:
        filters.append(LogEntry.trace_id == query.trace_id)
    total = await session.scalar(select(func.count()).select_from(LogEntry).where(*filters))
    entries = await session.scalars(
        select(LogEntry)
        .where(*filters)
        .order_by(LogEntry.ts.desc(), LogEntry.id.desc())
        .limit(query.limit)
        .offset(query.offset)
    )
    return LogPage(
        items=[LogEntryOut.model_validate(entry) for entry in entries],
        total=total,
        limit=query.limit,
        offset=query.offset,
    )


@router.get("/logs/facets")
async def log_facets(project: OwnedProject, session: SessionDep) -> LogFacets:
    """What the logs hold, for the explorer's filters: the values each field takes, with how
    many lines have them, and the time the logs cover."""
    mine = LogEntry.project_id == project.id
    total, first_ts, last_ts = (
        await session.execute(
            select(func.count(), func.min(LogEntry.ts), func.max(LogEntry.ts)).where(mine)
        )
    ).one()

    async def values(column) -> list[LogValue]:
        rows = await session.execute(
            select(column, func.count())
            .where(mine, column.is_not(None), column != "")
            .group_by(column)
            .order_by(func.count().desc(), column)
            .limit(FACET_SIZE)
        )
        found = [LogValue(value=value, count=count) for value, count in rows]
        return sorted(found, key=lambda item: (item.value.lower(), item.value))

    return LogFacets(
        total=total,
        first_ts=first_ts,
        last_ts=last_ts,
        # The level filter ignores case, so "error" and "ERROR" are one value.
        levels=await values(func.upper(LogEntry.level)),
        services=await values(LogEntry.service),
        routes=await values(LogEntry.route),
        error_types=await values(LogEntry.error_type),
    )
