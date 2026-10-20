"""Hybrid search against the test database, with a fake embedder and reranker (no credits)."""

import uuid

import pytest

from app.models import DocumentSourceType
from app.rag.chunkers import Chunk
from app.rag.ingest import store_document
from app.rag.retrieve import Ranking, rrf, search


def rrf_example():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    return (a, b, c), rrf([[a, b, c], [b, a]])


def test_rrf_scores_each_list_by_position():
    (a, b, c), scores = rrf_example()
    assert scores[c] == pytest.approx(1 / 63)  # 3rd in one list only
    assert scores[a] == pytest.approx(1 / 61 + 1 / 62)  # 1st and 2nd


def test_rrf_rewards_items_found_by_both_searches():
    (a, b, c), scores = rrf_example()
    assert scores[a] == pytest.approx(scores[b])  # 1st+2nd equals 2nd+1st
    assert scores[a] > scores[c]


def axis(i: int) -> list[float]:
    vector = [0.0] * 1024
    vector[i] = 1.0
    return vector


class FakeRouter:
    """Embeds by topic so 'meaning' is predictable; the reranker reply is configurable."""

    def __init__(self, ranking=None, fail=False):
        self.ranking, self.fail, self.rerank_calls = ranking, fail, 0

    async def embed(self, texts, name="embed"):
        return [axis(0 if "pool" in t.lower() else 1 if "token" in t.lower() else 2) for t in texts]

    async def complete(self, tier, messages, schema=None, **kwargs):
        self.rerank_calls += 1
        if self.fail:
            raise ValueError("model returned something unusable")

        class Result:
            parsed = Ranking(best_first=self.ranking)

        return Result()


DOCS = [
    (
        "docs/database.md",
        DocumentSourceType.DOC,
        "Database > Connection pooling",
        "Each instance keeps its own connection pool. Recommended pool size is 20 or less.",
    ),
    (
        "docs/authentication.md",
        DocumentSourceType.DOC,
        "Authentication > Time handling",
        "Session tokens expire after one hour; compare aware UTC datetimes.",
    ),
    (
        "src/config.py",
        DocumentSourceType.CODE,
        None,
        'DB_POOL_SIZE = int(os.getenv("PAYSTREAM_DB_POOL_SIZE", "20"))',
    ),
    (
        "docs/payments.md",
        DocumentSourceType.DOC,
        "Payments > Money handling",
        "All money is Decimal, never float.",
    ),
    (
        "INC-017",
        DocumentSourceType.INCIDENT,
        "INC-017",
        "Payment API 500s after the DB pool size was raised from 20 to 60.",
    ),
    (
        "docs/deployment.md",
        DocumentSourceType.DOC,
        "Deployment > Checklist",
        "Watch the error rate for 15 minutes after a rollout.",
    ),
]


@pytest.fixture
async def indexed(db_session, project):
    for path, source_type, section, content in DOCS:
        chunk = Chunk(content=content, start_line=5, end_line=9, section_path=section)
        await store_document(
            db_session,
            FakeRouter(),
            project.id,
            source_type,
            path,
            path,
            path,
            [chunk],
            "5eadee6abc",
        )
    await db_session.flush()
    return project


async def test_meaning_search_finds_the_pool_rule(db_session, indexed):
    hits = await search(
        db_session, FakeRouter(), indexed.id, "why is the pool exhausted?", use_reranker=False
    )
    assert hits[0].path in {"docs/database.md", "INC-017"}


async def test_keyword_search_finds_exact_identifiers(db_session, indexed):
    hits = await search(db_session, FakeRouter(), indexed.id, "DB_POOL_SIZE", use_reranker=False)
    assert "src/config.py" in [h.path for h in hits]


async def test_hits_carry_a_citation(db_session, indexed):
    hits = await search(db_session, FakeRouter(), indexed.id, "pool size", use_reranker=False)
    database = next(h for h in hits if h.path == "docs/database.md")
    assert database.citation == "docs/database.md § Database > Connection pooling L5-9 @ 5eadee6"


async def test_source_type_filter(db_session, indexed):
    hits = await search(
        db_session,
        FakeRouter(),
        indexed.id,
        "pool",
        source_types=[DocumentSourceType.INCIDENT],
        use_reranker=False,
    )
    assert [h.path for h in hits] == ["INC-017"]


async def test_reranker_order_is_used(db_session, indexed):
    router = FakeRouter(ranking=[5, 4])
    fused = await search(
        db_session, router, indexed.id, "pool tokens money", k=6, use_reranker=False
    )
    reranked = await search(db_session, router, indexed.id, "pool tokens money", k=2)
    assert [h.path for h in reranked] == [fused[5].path, fused[4].path]
    assert router.rerank_calls == 1


async def test_bad_reranker_answer_falls_back_to_fused_order(db_session, indexed):
    fused = await search(
        db_session, FakeRouter(), indexed.id, "pool tokens money", k=6, use_reranker=False
    )
    fallback = await search(db_session, FakeRouter(fail=True), indexed.id, "pool tokens money", k=2)
    assert [h.path for h in fallback] == [h.path for h in fused[:2]]
