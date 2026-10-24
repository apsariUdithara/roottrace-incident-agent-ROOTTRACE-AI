"""Ingestion against the test database, with a fake embedder (no credits)."""

from sqlalchemy import func, select

from app.models import Document, DocumentChunk, Incident, LogEntry
from app.rag.ingest import ingest_incidents, ingest_issues, ingest_logs, ingest_repo


class FakeEmbedder:
    def __init__(self):
        self.texts: list[str] = []

    async def embed(self, texts, name="embed"):
        self.texts += texts
        return [[0.1] * 1024 for _ in texts]


def make_repo(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "database.md").write_text(
        "# Database\n\n## Connection pooling\n\nKeep the pool at 20 or less.\n", encoding="utf-8"
    )
    (tmp_path / "config.py").write_text("POOL = 20\n\n\ndef pool_size():\n    return POOL\n")
    (tmp_path / "deploy.env").write_text("INSTANCES=5\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    return tmp_path


async def count(session, model, project):
    return await session.scalar(
        select(func.count()).select_from(model).where(model.project_id == project.id)
    )


async def test_repo_files_become_documents_and_chunks(db_session, project, tmp_path):
    embedder = FakeEmbedder()
    counts = await ingest_repo(db_session, embedder, project.id, make_repo(tmp_path))
    assert counts == {"indexed": 3, "unchanged": 0, "removed": 0}  # .git is skipped
    assert await count(db_session, DocumentChunk, project) == 5  # 2 sections + 2 code + 1 config


async def test_embedded_text_starts_with_its_location(db_session, project, tmp_path):
    embedder = FakeEmbedder()
    await ingest_repo(db_session, embedder, project.id, make_repo(tmp_path))
    assert "docs/database.md > Database > Connection pooling\n" in "\n".join(embedder.texts)


async def test_unchanged_files_are_skipped_and_cost_nothing(db_session, project, tmp_path):
    repo = make_repo(tmp_path)
    await ingest_repo(db_session, FakeEmbedder(), project.id, repo)
    second = FakeEmbedder()
    counts = await ingest_repo(db_session, second, project.id, repo)
    assert counts["unchanged"] == 3 and second.texts == []


async def test_changed_and_deleted_files_are_updated(db_session, project, tmp_path):
    repo = make_repo(tmp_path)
    await ingest_repo(db_session, FakeEmbedder(), project.id, repo)
    (repo / "config.py").write_text("POOL = 10\n")
    (repo / "deploy.env").unlink()
    counts = await ingest_repo(db_session, FakeEmbedder(), project.id, repo)
    assert counts == {"indexed": 1, "unchanged": 1, "removed": 1}
    contents = await db_session.scalars(
        select(DocumentChunk.content).where(DocumentChunk.project_id == project.id)
    )
    assert "POOL = 10" in contents.all()


INCIDENT = {
    "key": "INC-017",
    "date": "2026-06-16",
    "severity": "SEV1",
    "title": "Payment API 500s after pool size increase",
    "symptoms": "too many connections",
    "root_cause": "pool raised to 60",
    "fix": "restored 20",
    "tags": ["database"],
}


async def test_incidents_are_rows_and_searchable_documents(db_session, project):
    assert await ingest_incidents(db_session, FakeEmbedder(), project.id, [INCIDENT]) == 1
    incident = await db_session.scalar(select(Incident).where(Incident.project_id == project.id))
    assert incident.key == "INC-017" and incident.root_cause == "pool raised to 60"
    doc = await db_session.scalar(select(Document).where(Document.path == "INC-017"))
    assert doc is not None
    assert await ingest_incidents(db_session, FakeEmbedder(), project.id, [INCIDENT]) == 0


async def test_issues_become_documents(db_session, project):
    issue = {"number": 57, "title": "500s", "body": "after deploy", "created_at": "2026-10-06"}
    assert await ingest_issues(db_session, FakeEmbedder(), project.id, [issue]) == 1
    assert await db_session.scalar(select(Document).where(Document.path == "issue:57"))


LOG = {
    "ts": "2026-10-06T14:30:00.000Z",
    "level": "ERROR",
    "service": "paystream-api",
    "route": "/payments",
    "status": 500,
    "latency_ms": 5001,
    "message": "OperationalError: too many connections",
    "error_type": "OperationalError",
    "trace_id": "abc",
}


async def test_logs_load_and_reload_without_duplicates(db_session, project):
    rows = [LOG, {**LOG, "ts": "2026-10-06T14:31:00.000Z", "trace_id": "def"}]
    assert await ingest_logs(db_session, project.id, rows) == 2
    await ingest_logs(db_session, project.id, rows)
    assert await count(db_session, LogEntry, project) == 2
