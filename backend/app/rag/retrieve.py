"""Hybrid search over a project's chunks, with citations.

1. Two searches side by side, top 30 each:
   - meaning-based: pgvector cosine distance between the question and chunk embeddings,
   - keyword: Postgres full-text search, any of the question's words (OR), ranked.
2. Reciprocal Rank Fusion merges the two ranked lists into one.
3. Nemotron Nano reranks the top 20 and keeps the best k. If its answer is unusable, the
   fused order is kept, so search never fails because of the reranker.

Every hit carries its source (path, section or symbol, lines, commit) so it can be cited.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Float, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMRouter
from app.models import Document, DocumentChunk, DocumentSourceType

CANDIDATES = 30  # per search
RERANK_POOL = 20  # how many fused results the reranker sees
RRF_K = 60  # the standard Reciprocal Rank Fusion constant


@dataclass
class Hit:
    chunk_id: uuid.UUID
    path: str
    source_type: str
    content: str
    section_path: str | None
    symbol: str | None
    start_line: int | None
    end_line: int | None
    commit_sha: str | None
    score: float = 0.0

    @property
    def citation(self) -> str:
        where = f" § {self.section_path}" if self.section_path not in (None, self.path) else ""
        where = f" :: {self.symbol}" if self.symbol and not where else where
        lines = f" L{self.start_line}-{self.end_line}" if self.start_line else ""
        sha = f" @ {self.commit_sha[:7]}" if self.commit_sha else ""
        return f"{self.path}{where}{lines}{sha}"


def rrf(rankings: list[list[uuid.UUID]], k: int = RRF_K) -> dict[uuid.UUID, float]:
    """Merge ranked lists: each item earns 1 / (k + rank) from every list it appears in."""
    scores: dict[uuid.UUID, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] += 1 / (k + rank)

    return dict(scores)


def _scope(project_id: uuid.UUID, source_types: list[DocumentSourceType] | None):
    conditions = [DocumentChunk.project_id == project_id]
    if source_types:
        conditions.append(Document.source_type.in_(source_types))
    return conditions


async def vector_search(session, project_id, query_vector, source_types=None, limit=CANDIDATES):
    distance = DocumentChunk.embedding.cosine_distance(query_vector)
    rows = await session.execute(
        select(DocumentChunk.id)
        .join(Document)
        .where(*_scope(project_id, source_types), DocumentChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    return list(rows.scalars())


async def keyword_search(session, project_id, query, source_types=None, limit=CANDIDATES):
    # plainto_tsquery joins words with AND; swapping & for | matches chunks with ANY of them,
    # and ts_rank_cd then puts chunks that match more words, closer together, first.
    tsquery = text("replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery").bindparams(
        q=query
    )
    rank = func.ts_rank_cd(DocumentChunk.tsv, tsquery)
    rows = await session.execute(
        select(DocumentChunk.id)
        .join(Document)
        .where(*_scope(project_id, source_types), DocumentChunk.tsv.op("@@")(tsquery))
        .order_by(cast(rank, Float).desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def _load_hits(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, Hit]:
    rows = await session.execute(
        select(DocumentChunk, Document).join(Document).where(DocumentChunk.id.in_(ids))
    )
    return {
        chunk.id: Hit(
            chunk_id=chunk.id,
            path=doc.path,
            source_type=str(doc.source_type),
            content=chunk.content,
            section_path=chunk.section_path,
            symbol=chunk.symbol,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            commit_sha=doc.commit_sha,
        )
        for chunk, doc in rows.all()
    }


class Ranking(BaseModel):
    best_first: list[int]  # snippet numbers, most relevant first


async def rerank(router: LLMRouter, query: str, hits: list[Hit], k: int) -> list[Hit]:
    """Ask Nano to order the candidates; fall back to the fused order on any problem."""
    snippets = "\n\n".join(f"[{i}] {h.citation}\n{h.content[:400]}" for i, h in enumerate(hits))
    prompt = (
        f"Question: {query}\n\nSnippets:\n{snippets}\n\n"
        f"Return the numbers of the {k} snippets most useful for answering the question, "
        "most useful first."
    )
    try:
        result = await router.complete(
            "nano",
            [{"role": "user", "content": prompt}],
            schema=Ranking,
            max_tokens=800,
            name="rerank",
        )
        order = [i for i in dict.fromkeys(result.parsed.best_first) if 0 <= i < len(hits)]
    except Exception:  # noqa: BLE001 - the reranker is an optional improvement
        order = []
    rest = [i for i in range(len(hits)) if i not in order]
    return [hits[i] for i in order + rest][:k]


async def search(
    session: AsyncSession,
    router: LLMRouter,
    project_id: uuid.UUID,
    query: str,
    k: int = 5,
    source_types: list[DocumentSourceType] | None = None,
    use_reranker: bool = True,
) -> list[Hit]:
    """Find the k chunks most relevant to `query`, each with a citation."""
    [query_vector] = await router.embed([query], name="embed query")
    by_meaning = await vector_search(session, project_id, query_vector, source_types)
    by_keyword = await keyword_search(session, project_id, query, source_types)
    fused = rrf([by_meaning, by_keyword])
    order = sorted(fused, key=fused.get, reverse=True)[:RERANK_POOL]
    loaded = await _load_hits(session, order)
    hits = [loaded[i] for i in order]
    for h in hits:
        h.score = fused[h.chunk_id]
    if use_reranker and len(hits) > k:
        return await rerank(router, query, hits, k)
    return hits[:k]
