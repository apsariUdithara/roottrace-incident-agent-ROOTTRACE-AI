"""Load a project's knowledge into the database: chunk, embed, store.

- Repository files: markdown docs and Python code are chunked by structure; small config
  files (.env, .toml, .txt, .yml) become one chunk each.
- Past incidents and issues: one document and one chunk per record.
- Logs: rows in log_entries, queried with SQL later. Logs are never embedded.

Re-running is cheap: a document whose content hash is unchanged is skipped, so it costs
no embedding calls. Changed documents are replaced; deleted files are removed.
"""

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMRouter
from app.models import (
    Document,
    DocumentChunk,
    DocumentSourceType,
    Incident,
    IncidentStatus,
    LogEntry,
)
from app.rag.chunkers import Chunk, chunk_incident, chunk_issue, chunk_markdown, chunk_python

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "build",
    "dist",
}
CONFIG_SUFFIXES = {".env", ".toml", ".txt", ".yml", ".yaml", ".ini", ".cfg"}
REPO_TYPES = (DocumentSourceType.DOC, DocumentSourceType.CODE)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def chunk_file(path: Path, text: str) -> tuple[DocumentSourceType, list[Chunk]] | None:
    if path.suffix == ".md":
        return DocumentSourceType.DOC, chunk_markdown(text)
    if path.suffix == ".py":
        return DocumentSourceType.CODE, chunk_python(text)
    if path.suffix in CONFIG_SUFFIXES and text.strip():
        lines = text.count("\n") + 1
        return DocumentSourceType.CODE, [Chunk(content=text, start_line=1, end_line=lines)]
    return None


def text_to_embed(path: str, chunk: Chunk) -> str:
    """Prefix the chunk with where it lives, so its embedding carries that context too."""
    where = chunk.section_path or chunk.symbol
    return f"{path} > {where}\n{chunk.content}" if where else f"{path}\n{chunk.content}"


async def reuse_or_embed(
    session: AsyncSession, router: LLMRouter, path: str, chunks: list[Chunk]
) -> list[list[float]]:
    """Embeddings for `chunks`, reusing any already stored for the same path and text.

    Identical text gives an identical embedding, so a chunk unchanged since another indexed
    version (another release of the same repo) costs nothing. Only new text is embedded.
    """
    vectors: list[list[float] | None] = []
    for c in chunks:
        stored = await session.scalar(
            select(DocumentChunk.embedding)
            .join(Document)
            .where(
                Document.path == path,
                DocumentChunk.content == c.content,
                DocumentChunk.section_path.is_not_distinct_from(c.section_path),
                DocumentChunk.symbol.is_not_distinct_from(c.symbol),
                DocumentChunk.embedding.is_not(None),
            )
            .limit(1)
        )
        vectors.append(list(stored) if stored is not None else None)
    missing = [i for i, v in enumerate(vectors) if v is None]
    if missing:
        fresh = await router.embed(
            [text_to_embed(path, chunks[i]) for i in missing], name=f"embed {path}"
        )
        for i, vector in zip(missing, fresh, strict=True):
            vectors[i] = vector
    return vectors


async def store_document(
    session: AsyncSession,
    router: LLMRouter,
    project_id: uuid.UUID,
    source_type: DocumentSourceType,
    path: str,
    title: str | None,
    content_hash: str,
    chunks: list[Chunk],
    commit_sha: str | None = None,
) -> bool:
    """Insert or replace one document and its chunks. Returns False if it was unchanged."""
    existing = await session.scalar(
        select(Document).where(
            Document.project_id == project_id,
            Document.source_type == source_type,
            Document.path == path,
        )
    )
    if existing and existing.hash == content_hash:
        existing.commit_sha = commit_sha  # same content, maybe a new commit: keep citations current
        return False
    if existing:
        await session.delete(existing)  # its chunks go too (ON DELETE CASCADE)
        await session.flush()

    doc = Document(
        project_id=project_id,
        source_type=source_type,
        path=path,
        title=title,
        commit_sha=commit_sha,
        hash=content_hash,
    )
    session.add(doc)
    await session.flush()
    vectors = await reuse_or_embed(session, router, path, chunks)
    session.add_all(
        DocumentChunk(
            document_id=doc.id,
            project_id=project_id,
            content=c.content,
            section_path=c.section_path,
            start_line=c.start_line,
            end_line=c.end_line,
            symbol=c.symbol,
            embedding=vector,
        )
        for c, vector in zip(chunks, vectors, strict=True)
    )
    return True


async def ingest_repo(
    session: AsyncSession,
    router: LLMRouter,
    project_id: uuid.UUID,
    root: Path,
    commit_sha: str | None = None,
) -> dict[str, int]:
    counts = {"indexed": 0, "unchanged": 0, "removed": 0}
    seen: set[str] = set()
    for file in sorted(root.rglob("*")):
        if not file.is_file() or SKIP_DIRS & set(file.relative_to(root).parts):
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        result = chunk_file(file, text)
        if not result or not result[1]:
            continue
        source_type, chunks = result
        rel = file.relative_to(root).as_posix()
        seen.add(rel)
        changed = await store_document(
            session,
            router,
            project_id,
            source_type,
            rel,
            file.name,
            sha256(text),
            chunks,
            commit_sha,
        )
        counts["indexed" if changed else "unchanged"] += 1

    gone = await session.scalars(
        select(Document).where(
            Document.project_id == project_id,
            Document.source_type.in_(REPO_TYPES),
            Document.path.not_in(seen or {""}),
        )
    )
    for doc in gone:
        await session.delete(doc)
        counts["removed"] += 1
    await session.flush()
    return counts


async def ingest_incidents(
    session: AsyncSession, router: LLMRouter, project_id: uuid.UUID, records: list[dict]
) -> int:
    indexed = 0
    for r in records:
        incident = await session.scalar(
            select(Incident).where(Incident.project_id == project_id, Incident.key == r["key"])
        )
        if incident is None:
            incident = Incident(project_id=project_id, key=r["key"], title=r["title"])
            session.add(incident)
        incident.title = r["title"]
        incident.severity = r.get("severity")
        incident.symptoms = r.get("symptoms")
        incident.root_cause = r.get("root_cause")
        incident.fix = r.get("fix")
        incident.tags = r.get("tags", [])
        incident.status = IncidentStatus.RESOLVED  # these are past, closed incidents
        if r.get("date"):
            incident.occurred_at = datetime.fromisoformat(r["date"]).replace(tzinfo=UTC)
        chunk = chunk_incident(r)
        indexed += await store_document(
            session,
            router,
            project_id,
            DocumentSourceType.INCIDENT,
            r["key"],
            r["title"],
            sha256(json.dumps(r, sort_keys=True)),
            [chunk],
        )
    await session.flush()
    return indexed


async def ingest_issues(
    session: AsyncSession, router: LLMRouter, project_id: uuid.UUID, records: list[dict]
) -> int:
    indexed = 0
    for r in records:
        indexed += await store_document(
            session,
            router,
            project_id,
            DocumentSourceType.ISSUE,
            f"issue:{r['number']}",
            r["title"],
            sha256(json.dumps(r, sort_keys=True)),
            [chunk_issue(r)],
        )
    await session.flush()
    return indexed


async def ingest_logs(session: AsyncSession, project_id: uuid.UUID, rows: Iterable[dict]) -> int:
    """Replace the project's logs in this file's time window with the file's rows."""
    entries = [
        {
            "project_id": project_id,
            "ts": datetime.fromisoformat(r["ts"]),
            "level": r["level"],
            "service": r.get("service"),
            "instance": r.get("instance"),
            "route": r.get("route"),
            "status": r.get("status"),
            "latency_ms": r.get("latency_ms"),
            "message": r.get("message", ""),
            "error_type": r.get("error_type"),
            "trace_id": r.get("trace_id"),
        }
        for r in rows
    ]
    if not entries:
        return 0
    first, last = min(e["ts"] for e in entries), max(e["ts"] for e in entries)
    await session.execute(
        delete(LogEntry).where(
            LogEntry.project_id == project_id, LogEntry.ts >= first, LogEntry.ts <= last
        )
    )
    await session.execute(insert(LogEntry), entries)
    return len(entries)
