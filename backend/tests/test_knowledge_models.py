import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EMBEDDING_DIMENSIONS,
    Document,
    DocumentChunk,
    DocumentSourceType,
    Incident,
    IncidentStatus,
    Project,
)


def unit_vector(axis: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[axis] = 1.0
    return vector


async def add_document(session: AsyncSession, project: Project) -> Document:
    document = Document(
        project_id=project.id,
        source_type=DocumentSourceType.DOC,
        path="docs/database.md",
        hash="sha256-of-content",
    )
    session.add(document)
    await session.flush()
    return document


async def add_chunk(
    session: AsyncSession, document: Document, content: str, **fields: object
) -> DocumentChunk:
    chunk = DocumentChunk(
        document_id=document.id, project_id=document.project_id, content=content, **fields
    )
    session.add(chunk)
    await session.flush()
    return chunk


async def test_full_text_search_matches_headings_and_symbols(
    db_session: AsyncSession, project: Project
) -> None:
    document = await add_document(db_session, project)
    pooling = await add_chunk(
        db_session,
        document,
        "Keep the pool at or below 20 per instance.",
        section_path="Database > Connection pooling",
    )
    await add_chunk(db_session, document, "Back up nightly.", section_path="Database > Backups")
    code = await add_chunk(
        db_session, document, "return round(sum(prices), 2)", symbol="calculate_total"
    )

    async def search(query: str) -> set:
        rows = await db_session.scalars(
            text("SELECT id FROM document_chunks WHERE tsv @@ plainto_tsquery('english', :q)"),
            {"q": query},
        )
        return set(rows)

    assert await search("connection pooling") == {pooling.id}
    assert await search("calculate_total") == {code.id}


async def test_embeddings_rank_by_cosine_distance(
    db_session: AsyncSession, project: Project
) -> None:
    document = await add_document(db_session, project)
    far = await add_chunk(db_session, document, "far", embedding=unit_vector(1))
    near = await add_chunk(db_session, document, "near", embedding=unit_vector(0))

    ranked = await db_session.scalars(
        select(DocumentChunk.id)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.embedding.cosine_distance(unit_vector(0)))
    )

    assert list(ranked) == [near.id, far.id]


async def test_embedding_size_is_enforced(db_session: AsyncSession, project: Project) -> None:
    document = await add_document(db_session, project)

    with pytest.raises((StatementError, ValueError), match="expected 1024 dimensions"):
        await add_chunk(db_session, document, "wrong size", embedding=[0.1, 0.2, 0.3])


async def test_incident_keys_are_unique_per_project(
    db_session: AsyncSession, project: Project
) -> None:
    incident = Incident(project_id=project.id, key="INC-017", title="Pool exhaustion")
    db_session.add(incident)
    await db_session.flush()
    await db_session.refresh(incident)
    assert (incident.status, incident.tags) == (IncidentStatus.OPEN, [])

    db_session.add(Incident(project_id=project.id, key="INC-017", title="Duplicate"))
    with pytest.raises(IntegrityError, match="uq_incidents_project_id_key"):
        await db_session.flush()
