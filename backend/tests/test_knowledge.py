"""POST /projects/{id}/ingest and GET /projects/{id}/knowledge.

Indexing runs for real (git clone, archive, chunking) against a local repository that stands in
for GitHub. Only the embedding model is fake.
"""

import asyncio
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import investigations
from app.models import Document, DocumentChunk, DocumentSourceType, IngestStatus, Repository
from app.rag import indexing
from tests.support import FakeEmbedder, db, new_project

NewUser = Callable[[], dict[str, str]]
GITHUB = "https://github.com/acme/paylite"
FINISHED = ("indexed", "failed")
FILES = {
    "README.md": "# PayLite\n\n## Pool\n\nKeep the pool at 20 connections.\n",
    "app/pool.py": "def size():\n    return 20\n",
    "logo.png": "not indexed: not a doc, code or config file",
}


AUTHOR = ["-c", "user.name=Maya", "-c", "user.email=maya@example.com"]
DATES = {"GIT_AUTHOR_DATE": "2026-10-01T10:00:00Z", "GIT_COMMITTER_DATE": "2026-10-01T10:00:00Z"}


def git(repo: Path, *args: str) -> str:
    command = ["git", "-C", str(repo), *AUTHOR, *args]
    env = {**os.environ, **DATES}
    return subprocess.run(
        command, check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def unique(text: str) -> str:
    """An embedding is reused for identical text in any project, so a test that needs real
    embedding calls needs text that no other test has indexed."""
    return f"{text}  # {uuid.uuid4().hex[:8]}\n"


def commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    """Write (or, for None, delete) files and commit them; returns the commit's sha."""
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository standing in for GitHub: cloning any github.com URL clones it. Clones go in
    tmp_path/repos."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit(repo, FILES, "first")
    monkeypatch.setattr(indexing, "remote_url", lambda url: str(repo))
    monkeypatch.setattr(indexing, "repos_dir", lambda: tmp_path / "repos")
    return repo


@pytest.fixture
def alice(client: TestClient, new_user: NewUser) -> dict[str, Any]:
    headers = new_user()
    return {"headers": headers, "project_id": new_project(client, headers)}


def connect(client: TestClient, alice: dict, **fields: Any) -> dict:
    response = client.post(
        f"/projects/{alice['project_id']}/repositories",
        headers=alice["headers"],
        json={"url": GITHUB, **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


def ingest(client: TestClient, alice: dict):
    return client.post(f"/projects/{alice['project_id']}/ingest", headers=alice["headers"])


def wait_until_finished(client: TestClient, alice: dict, timeout: float = 30) -> dict:
    """Polls the repository, as the Knowledge page does, until indexing has finished."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(
            f"/projects/{alice['project_id']}/repositories", headers=alice["headers"]
        )
        repository = response.json()[0]
        if repository["ingest_status"] in FINISHED and not indexing.RUNNING:
            return repository
        time.sleep(0.05)
    raise AssertionError(f"indexing didn't finish in {timeout} s")


def knowledge(client: TestClient, alice: dict, query: str = "") -> dict:
    url = f"/projects/{alice['project_id']}/knowledge{query}"
    response = client.get(url, headers=alice["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def stored(repository_id: str) -> Repository:
    return db(lambda session: session.get(Repository, uuid.UUID(repository_id)))


# --- Indexing ----------------------------------------------------------------------------------


def test_ingest_clones_and_indexes_the_default_branch(
    client: TestClient, alice: dict, upstream: Path, embedder: FakeEmbedder, tmp_path: Path
):
    sha = git(upstream, "rev-parse", "HEAD")
    repository = connect(client, alice)

    started = ingest(client, alice)

    assert started.status_code == 202, started.text
    assert [r["ingest_status"] for r in started.json()] == ["queued"]
    done = wait_until_finished(client, alice)
    assert (done["ingest_status"], done["ingest_error"]) == ("indexed", None)
    assert done["last_indexed_sha"] == sha
    assert done["last_indexed_at"] is not None
    # A bare clone in the data directory, which the agent reads from now on.
    clone = Path(stored(repository["id"]).local_path)
    assert clone == tmp_path / "repos" / f"{repository['id']}.git"
    assert git(clone, "rev-parse", "--is-bare-repository") == "true"

    items = knowledge(client, alice)["items"]
    assert [(i["source_type"], i["path"], i["source_ref"]) for i in items] == [
        ("code", "app/pool.py", f"file:app/pool.py@{sha}"),
        ("doc", "README.md", "doc:README.md"),
    ]
    assert all(item["chunk_count"] >= 1 and item["commit_sha"] == sha for item in items)


def test_reindexing_fetches_new_commits_and_only_embeds_what_changed(
    client: TestClient, alice: dict, upstream: Path, embedder: FakeEmbedder
):
    connect(client, alice)
    ingest(client, alice)
    wait_until_finished(client, alice)
    first = len(embedder.texts)
    changes = {
        "app/pool.py": unique("def size():\n    return 5"),
        "docs/runbook.md": unique("# Run"),
    }
    sha = commit(upstream, {"README.md": None, **changes}, "second")

    ingest(client, alice)
    done = wait_until_finished(client, alice)

    assert (done["ingest_status"], done["last_indexed_sha"]) == ("indexed", sha)
    paths = [item["path"] for item in knowledge(client, alice)["items"]]
    assert paths == ["app/pool.py", "docs/runbook.md"]  # README.md was deleted upstream
    assert len(embedder.texts) - first == 2  # the changed file and the new one


def test_a_repository_without_a_remote_is_used_as_it_is(
    client: TestClient, alice: dict, upstream: Path, embedder: FakeEmbedder
):
    """Like the demo's local PayStream build: indexed at its tag, never cloned or changed."""
    release = git(upstream, "rev-parse", "HEAD")
    git(upstream, "tag", "v1.0")
    commit(upstream, {"app/pool.py": "def size():\n    return 99\n"}, "after the release")
    repository = connect(client, alice, default_branch="v1.0")

    async def point_at_upstream(session: AsyncSession) -> None:
        (await session.get(Repository, uuid.UUID(repository["id"]))).local_path = str(upstream)

    db(point_at_upstream)

    ingest(client, alice)
    done = wait_until_finished(client, alice)

    assert (done["ingest_status"], done["last_indexed_sha"]) == ("indexed", release)
    assert stored(repository["id"]).local_path == str(upstream)
    assert git(upstream, "status", "--porcelain") == ""  # its working tree wasn't touched
    assert git(upstream, "branch", "--show-current") == "main"


def test_a_clone_cut_off_halfway_is_made_again(
    client: TestClient, alice: dict, upstream: Path, embedder: FakeEmbedder, tmp_path: Path
):
    """Even inside another repository (in development, data/ is inside RootTrace's own)."""
    git(tmp_path, "init", "-q")
    repository = connect(client, alice)
    (tmp_path / "repos" / f"{repository['id']}.git").mkdir(parents=True)  # empty: cut off

    ingest(client, alice)
    done = wait_until_finished(client, alice)

    assert done["ingest_status"] == "indexed", done["ingest_error"]
    assert done["last_indexed_sha"] == git(upstream, "rev-parse", "HEAD")


def outage() -> openai.APIConnectionError:
    request = httpx2.Request("POST", "https://api.tokenfactory.nebius.com/v1/embeddings")
    return openai.APIConnectionError(request=request)


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (
            {"default_branch": "release"},
            "The branch release wasn't found in the repository. "
            "Check the default branch in the project settings.",
        ),
        ({"remote": "missing"}, "git clone failed: "),
        ({"embedding": "down"}, "The embedding model didn't answer. Start the indexing again"),
        ({"max_files": 1}, "The repository has 2 files to index, and the limit is 1."),
    ],
    ids=["unknown branch", "clone fails", "embedding model down", "too many files"],
)
def test_a_failed_run_says_why_and_indexes_nothing(
    client: TestClient,
    alice: dict,
    upstream: Path,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    setup: dict,
    reason: str,
):
    if "remote" in setup:
        monkeypatch.setattr(indexing, "remote_url", lambda url: str(tmp_path / "missing"))
    if "embedding" in setup:
        commit(upstream, {"app/pool.py": unique("def size():\n    return 20")}, "new text")
        embedder.fail = outage()
    if "max_files" in setup:
        monkeypatch.setattr(indexing, "MAX_FILES", setup["max_files"])
    repository = connect(client, alice, default_branch=setup.get("default_branch", "main"))

    ingest(client, alice)
    done = wait_until_finished(client, alice)

    assert done["ingest_status"] == "failed"
    assert done["ingest_error"].startswith(reason)
    assert done["last_indexed_at"] is None
    assert knowledge(client, alice)["total"] == 0
    assert stored(repository["id"]).local_path is None  # investigations still can't start


def test_a_double_click_starts_one_run(
    client: TestClient, alice: dict, monkeypatch: pytest.MonkeyPatch, embedder: FakeEmbedder
):
    runs: list[uuid.UUID] = []
    finish = threading.Event()

    async def slow_run(repository_id, router):
        runs.append(repository_id)
        while not finish.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(indexing, "run", slow_run)
    connect(client, alice)

    first, second = ingest(client, alice), ingest(client, alice)

    assert (first.status_code, second.status_code) == (202, 202)
    assert len(runs) == 1
    finish.set()
    deadline = time.monotonic() + 5
    while indexing.RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not indexing.RUNNING


def test_investigations_wait_for_the_first_index(
    client: TestClient,
    alice: dict,
    upstream: Path,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(investigations, "start_in_background", lambda *args: None)
    url = f"/projects/{alice['project_id']}/investigations"
    question = {"question": "Why are payments failing?"}
    connect(client, alice)

    before = client.post(url, headers=alice["headers"], json=question)
    ingest(client, alice)
    wait_until_finished(client, alice)
    after = client.post(url, headers=alice["headers"], json=question)

    assert before.status_code == 409
    assert before.json()["detail"] == "Index a repository for this project first"
    assert after.status_code == 202, after.text


def test_a_run_cut_off_by_a_restart_is_marked_failed(client: TestClient, alice: dict):
    repository = connect(client, alice)

    async def restart_while_running(session: AsyncSession) -> None:
        row = await session.get(Repository, uuid.UUID(repository["id"]))
        row.ingest_status = IngestStatus.RUNNING
        await session.commit()
        await indexing.mark_interrupted(session)  # what the app does when it starts

    db(restart_while_running)

    after = client.get(f"/projects/{alice['project_id']}/repositories", headers=alice["headers"])
    assert after.json()[0]["ingest_status"] == "failed"
    assert after.json()[0]["ingest_error"] == (
        "Indexing was interrupted by a server restart. Start it again."
    )


def test_ingest_needs_a_repository(client: TestClient, alice: dict, embedder: FakeEmbedder):
    response = ingest(client, alice)

    assert response.status_code == 409
    assert response.json()["detail"] == "Connect a repository to this project first"


def test_the_token_is_sent_in_a_header_and_never_saved(tmp_path: Path):
    async def configured_header() -> str:
        return await indexing.git(None, "config", "--get", "http.extraHeader", token="ghp_secret")

    header = asyncio.run(configured_header()).strip()
    assert header == "Authorization: Basic eC1hY2Nlc3MtdG9rZW46Z2hwX3NlY3JldA=="  # x-access-token
    assert "ghp_secret" not in indexing.git_failure(
        "fetch", "fatal: ghp_secret leaked", "ghp_secret"
    )
    assert indexing.git_failure(
        "clone", "remote: Repository not found.\nfatal: repository not found", None
    ).startswith("GitHub didn't let RootTrace read the repository.")


def test_disconnecting_deletes_the_clone_but_never_a_repository_it_didnt_make(
    client: TestClient, alice: dict, upstream: Path, embedder: FakeEmbedder
):
    repository = connect(client, alice)
    ingest(client, alice)
    wait_until_finished(client, alice)
    clone = Path(stored(repository["id"]).local_path)
    base = f"/projects/{alice['project_id']}/repositories"

    client.delete(f"{base}/{repository['id']}", headers=alice["headers"])

    assert not clone.exists()
    demo = connect(client, alice)

    async def point_at_upstream(session: AsyncSession) -> None:
        (await session.get(Repository, uuid.UUID(demo["id"]))).local_path = str(upstream)

    db(point_at_upstream)
    client.delete(f"{base}/{demo['id']}", headers=alice["headers"])
    assert (upstream / ".git").exists()


# --- Listing what's indexed --------------------------------------------------------------------


def add_documents(project_id: str, *documents: tuple[DocumentSourceType, str, str | None]) -> None:
    async def add(session: AsyncSession) -> None:
        for source_type, path, title in documents:
            document = Document(
                project_id=uuid.UUID(project_id),
                source_type=source_type,
                path=path,
                title=title,
                hash="h",
            )
            session.add(document)
            await session.flush()
            for n in range(2):
                session.add(
                    DocumentChunk(
                        document_id=document.id, project_id=document.project_id, content=f"{n}"
                    )
                )

    db(add)


@pytest.fixture
def indexed(alice: dict) -> dict:
    add_documents(
        alice["project_id"],
        (DocumentSourceType.DOC, "docs/pool_sizing.md", "Pool sizing"),
        (DocumentSourceType.DOC, "README.md", "README.md"),
        (DocumentSourceType.CODE, "app/db.py", "db.py"),
        (DocumentSourceType.INCIDENT, "INC-017", "Pool exhausted"),
        (DocumentSourceType.ISSUE, "issue:42", "500s after deploy"),
    )
    return alice


def test_lists_everything_indexed_by_type_with_refs(client: TestClient, indexed: dict):
    page = knowledge(client, indexed)

    assert (page["total"], page["limit"], page["offset"]) == (5, 50, 0)
    assert [(i["path"], i["source_ref"], i["chunk_count"]) for i in page["items"]] == [
        ("app/db.py", "file:app/db.py@HEAD", 2),
        ("docs/pool_sizing.md", "doc:docs/pool_sizing.md", 2),
        ("README.md", "doc:README.md", 2),
        ("INC-017", "incident:INC-017", 2),
        ("issue:42", "issue:42", 2),
    ]


def test_filters_by_type_and_text(client: TestClient, indexed: dict):
    docs = knowledge(client, indexed, "?type=doc")
    pool = knowledge(client, indexed, "?q=POOL")
    literal = knowledge(client, indexed, "?q=pool_")  # "_" is a character, not a wildcard

    assert [i["path"] for i in docs["items"]] == ["docs/pool_sizing.md", "README.md"]
    assert [i["path"] for i in pool["items"]] == ["docs/pool_sizing.md", "INC-017"]
    assert [i["path"] for i in literal["items"]] == ["docs/pool_sizing.md"]
    assert knowledge(client, indexed, "?q=%25")["total"] == 0  # nor is "%"


def test_pages_through_the_list(client: TestClient, indexed: dict):
    page = knowledge(client, indexed, "?limit=2&offset=2")

    assert (page["total"], page["limit"], page["offset"]) == (5, 2, 2)
    assert [i["path"] for i in page["items"]] == ["README.md", "INC-017"]


def test_rejects_bad_queries(client: TestClient, alice: dict):
    for query in ("?type=video", "?limit=0", "?limit=201", "?offset=-1"):
        url = f"/projects/{alice['project_id']}/knowledge{query}"
        response = client.get(url, headers=alice["headers"])
        assert response.status_code == 422, query


def test_another_users_knowledge_is_invisible(client: TestClient, indexed: dict, new_user: NewUser):
    bob = new_user()
    base = f"/projects/{indexed['project_id']}"

    for response in (
        client.get(f"{base}/knowledge", headers=bob),
        client.post(f"{base}/ingest", headers=bob),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found", "code": "not_found"}
    for response in (client.get(f"{base}/knowledge"), client.post(f"{base}/ingest")):
        assert response.status_code == 401

    other = new_project(client, indexed["headers"], "Refunds")
    assert knowledge(client, {**indexed, "project_id": other})["total"] == 0
