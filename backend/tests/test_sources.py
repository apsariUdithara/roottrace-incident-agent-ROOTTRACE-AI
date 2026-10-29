"""GET /projects/{id}/sources?ref=: opening a source_ref in the source viewer.

The repository is a real local git repository. Its first commit is the indexed one, and a later
commit stands for what the clone's HEAD moved on to, so a ref without a version must still open
the indexed code.
"""

import os
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Document,
    DocumentChunk,
    DocumentSourceType,
    Incident,
    LogEntry,
    Memory,
    MemoryAuthor,
    MemoryCategory,
    Repository,
)
from app.rag import sources
from tests.support import db, new_project

NewUser = Callable[[], dict[str, str]]
README = "# PayLite\n\nIntro.\n\n## Pool sizing\n\nKeep the pool at 20 connections.\n"
POOL = "import os\n\n\ndef size():\n    return 20\n"
DEPLOY_AT = datetime(2026, 10, 6, 14, 20, tzinfo=UTC)


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-10-01T10:00:00Z",
        "GIT_COMMITTER_DATE": "2026-10-01T10:00:00Z",
    }
    command = ["git", "-C", str(repo), "-c", "user.name=Maya", "-c", "user.email=maya@x", *args]
    return subprocess.run(
        command, check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def write(repo: Path, files: dict[str, str | bytes]) -> None:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "paylite"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    write(path, {"README.md": README, "app/pool.py": POOL, "logo.png": b"\x89PNG\0\0"})
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "First release")
    git(path, "tag", "v1.0")
    indexed = git(path, "rev-parse", "HEAD")
    write(path, {"app/pool.py": POOL.replace("20", "100")})
    git(path, "commit", "-q", "-am", "Raise the pool to 100\n\nMore traffic this week.")
    return {"path": path, "indexed": indexed, "later": git(path, "rev-parse", "HEAD")}


@pytest.fixture
def alice(client: TestClient, new_user: NewUser) -> dict[str, Any]:
    headers = new_user()
    return {"headers": headers, "project_id": new_project(client, headers)}


@pytest.fixture
def indexed(client: TestClient, alice: dict, repo: dict) -> dict[str, Any]:
    """Alice's project with the repository indexed at its first commit, plus an issue, an
    incident, log lines and a memory."""
    response = client.post(
        f"/projects/{alice['project_id']}/repositories",
        headers=alice["headers"],
        json={"url": "https://github.com/acme/paylite"},
    )
    assert response.status_code == 201, response.text
    project_id = uuid.UUID(alice["project_id"])
    repository_id = uuid.UUID(response.json()["id"])
    memory_id = uuid.uuid4()

    async def add(session: AsyncSession) -> None:
        repository = await session.get(Repository, repository_id)
        repository.local_path, repository.last_indexed_sha = str(repo["path"]), repo["indexed"]
        for source_type, path, title in (
            (DocumentSourceType.DOC, "README.md", "PayLite"),
            (DocumentSourceType.CODE, "app/pool.py", "pool.py"),
        ):
            session.add(
                Document(
                    project_id=project_id,
                    source_type=source_type,
                    path=path,
                    title=title,
                    commit_sha=repo["indexed"],
                    hash="h",
                )
            )
        issue = Document(
            project_id=project_id,
            source_type=DocumentSourceType.ISSUE,
            path="issue:41",
            title="Checkout returns 500",
            hash="h",
        )
        session.add(issue)
        await session.flush()
        session.add(
            DocumentChunk(
                document_id=issue.id,
                project_id=project_id,
                content="Issue #41 (closed, 2026-10-06)\nCheckout returns 500\nSince the deploy.",
            )
        )
        session.add(Incident(project_id=project_id, key="INC-017", title="Pool exhausted"))
        session.add_all(
            LogEntry(project_id=project_id, ts=ts, level=level, message=message, trace_id=trace)
            for ts, level, message, trace in (
                (DEPLOY_AT, "INFO", "deploy started: release v1.0", None),
                (DEPLOY_AT.replace(minute=25), "ERROR", "too many connections", "t-1"),
                (DEPLOY_AT.replace(minute=24), "INFO", "POST /payments", "t-1"),
                (DEPLOY_AT.replace(minute=26), "ERROR", "too many connections", "t-2"),
            )
        )
        session.add(
            Memory(
                id=memory_id,
                project_id=project_id,
                category=MemoryCategory.PROJECT_FACT,
                content="Payments use PostgreSQL",
                created_by=MemoryAuthor.AGENT,
            )
        )

    db(add)
    return {**alice, "memory_id": str(memory_id), **repo}


def open_source(client: TestClient, project: dict, ref: str, status: int = 200) -> dict:
    response = client.get(
        f"/projects/{project['project_id']}/sources",
        headers=project["headers"],
        params={"ref": ref},
    )
    assert response.status_code == status, response.text
    return response.json()


# --- Files and docs ----------------------------------------------------------------------------


def test_opens_a_file_at_the_version_the_ref_names_with_its_lines(
    client: TestClient, indexed: dict
):
    source = open_source(client, indexed, f"file:app/pool.py#L4-L5@{indexed['later']}")

    assert source == {
        "ref": f"file:app/pool.py#L4-L5@{indexed['later']}",
        "kind": "file",
        "path": "app/pool.py",
        "sha": indexed["later"],
        "language": "python",
        "content": POOL.replace("20", "100"),
        "start_line": 4,
        "end_line": 5,
    }


def test_opens_the_agents_refs_to_files(client: TestClient, indexed: dict):
    """get_file's ref names a tag; a search hit's names a short sha and a symbol."""
    by_tag = open_source(client, indexed, "app/pool.py L4-5 @ v1.0")
    by_hit = open_source(client, indexed, f"app/pool.py :: size L4-5 @ {indexed['indexed'][:7]}")

    for source in (by_tag, by_hit):
        assert (source["kind"], source["sha"], source["content"]) == (
            "file",
            indexed["indexed"],
            POOL,
        )
        assert (source["start_line"], source["end_line"]) == (4, 5)


def test_a_ref_without_a_version_opens_what_was_indexed(client: TestClient, indexed: dict):
    """The clone's HEAD has moved on; the Knowledge list's `@HEAD` still means the indexed code."""
    for ref in ("file:app/pool.py@HEAD", "file:app/pool.py", "app/pool.py"):
        source = open_source(client, indexed, ref)
        assert (source["sha"], source["content"]) == (indexed["indexed"], POOL), ref
        assert source["start_line"] is None


def test_lines_past_the_end_are_clamped_or_dropped(client: TestClient, indexed: dict):
    clamped = open_source(client, indexed, "file:app/pool.py#L4-L99")
    beyond = open_source(client, indexed, "file:app/pool.py#L50-L60")

    assert (clamped["start_line"], clamped["end_line"]) == (4, 5)
    assert (beyond["start_line"], beyond["end_line"]) == (None, None)


def test_opens_a_doc_and_its_section(client: TestClient, indexed: dict):
    whole = open_source(client, indexed, "doc:README.md")
    cited = open_source(
        client, indexed, f"README.md § PayLite > Pool sizing L5-7 @ {indexed['indexed'][:7]}"
    )

    assert whole == {
        "ref": "doc:README.md",
        "kind": "doc",
        "path": "README.md",
        "section": None,
        "title": "PayLite",
        "markdown": README,
    }
    assert (cited["kind"], cited["section"], cited["markdown"]) == ("doc", "pool-sizing", README)
    assert open_source(client, indexed, "doc:README.md#pool-sizing")["section"] == "pool-sizing"


# --- Commits -----------------------------------------------------------------------------------


def test_opens_a_commit_by_either_kind_of_ref(client: TestClient, indexed: dict):
    for ref in (f"commit {indexed['later'][:10]}", f"commit:{indexed['later']}"):
        source = open_source(client, indexed, ref)

        assert source["sha"] == indexed["later"]
        assert source["message"] == "Raise the pool to 100\n\nMore traffic this week."
        assert (source["author"], source["files"]) == ("Maya", ["app/pool.py"])
        assert source["committed_at"].startswith("2026-10-01T10:00:00")
        assert "-    return 20\n+    return 100" in source["diff"]


# --- From the database -------------------------------------------------------------------------


def test_opens_the_log_lines_of_a_trace_oldest_first(client: TestClient, indexed: dict):
    for ref in ("log trace_id=t-1", "log:t-1"):
        source = open_source(client, indexed, ref)
        assert (source["kind"], source["trace_id"]) == ("log", "t-1")
        assert [e["message"] for e in source["entries"]] == [
            "POST /payments",
            "too many connections",
        ]


def test_opens_a_deploy_line(client: TestClient, indexed: dict):
    source = open_source(client, indexed, f"log deploy at {DEPLOY_AT.isoformat()}")

    assert (source["kind"], source["trace_id"]) == ("log", None)
    assert [e["message"] for e in source["entries"]] == ["deploy started: release v1.0"]


def test_opens_an_incident_an_issue_and_a_memory(client: TestClient, indexed: dict):
    for ref in ("INC-017", "incident:inc-017"):
        incident = open_source(client, indexed, ref)
        assert (incident["kind"], incident["incident"]["title"]) == ("incident", "Pool exhausted")

    issue = open_source(client, indexed, "issue:41")
    assert {k: v for k, v in issue.items() if k != "ref"} == {
        "kind": "issue",
        "number": 41,
        "title": "Checkout returns 500",
        "body": "Since the deploy.",
        "state": "closed",
        "url": "https://github.com/acme/paylite/issues/41",
    }

    memory = open_source(client, indexed, f"memory:{indexed['memory_id']}")
    assert (memory["kind"], memory["memory"]["content"]) == ("memory", "Payments use PostgreSQL")
    assert memory["memory"]["created_by"] == "agent"


# --- What doesn't open -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "app/missing.py L1-2 @ v1.0",
        "file:app/pool.py@v9.9",
        "file:app/pool.py@--output=/tmp/x",  # never passed to git as an option
        "file:app/pool.py@main..v1.0",
        "file:../outside.py",
        "file:/etc/passwd",
        "app",  # a folder
        "commit 0123456789",
        "commit:not-a-sha",
        "sql: SELECT count(*) FROM payments",
        "investigation:123",
        "log trace_id=nope",
        "log deploy at 2026-10-06T15:00:00+00:00",
        "log deploy at yesterday",
        "INC-999",
        "issue:999",
        "issue:abc",
        f"memory:{uuid.uuid4()}",
        "memory:not-an-id",
    ],
)
def test_refs_that_dont_resolve_give_404(client: TestClient, indexed: dict, ref: str):
    body = open_source(client, indexed, ref, status=404)

    assert body["code"] == "not_found"
    assert body["detail"]  # a reason the viewer can show


def test_files_too_large_or_binary_to_show_give_422(
    client: TestClient, indexed: dict, monkeypatch: pytest.MonkeyPatch
):
    assert "binary" in open_source(client, indexed, "file:logo.png", status=422)["detail"]
    monkeypatch.setattr(sources, "MAX_FILE_BYTES", 10)
    assert "too large" in open_source(client, indexed, "file:app/pool.py", status=422)["detail"]


def test_code_needs_an_indexed_repository_but_the_rest_doesnt(client: TestClient, alice: dict):
    project_id = uuid.UUID(alice["project_id"])
    db(lambda session: _add(session, Incident(project_id=project_id, key="INC-1", title="Down")))

    body = open_source(client, alice, "commit:abcdef1", status=409)
    assert body["detail"] == "Index the project's repository first"
    assert open_source(client, alice, "INC-1")["incident"]["title"] == "Down"


async def _add(session: AsyncSession, row: Any) -> None:
    session.add(row)


def test_an_issue_without_a_known_state_or_repository_says_so(client: TestClient, alice: dict):
    project_id = uuid.UUID(alice["project_id"])

    async def add(session: AsyncSession) -> None:
        issue = Document(
            project_id=project_id, source_type=DocumentSourceType.ISSUE, path="issue:7", hash="h"
        )
        session.add(issue)
        await session.flush()
        session.add(
            DocumentChunk(
                document_id=issue.id, project_id=project_id, content="Issue #7\nSlow search\nBody"
            )
        )

    db(add)
    issue = open_source(client, alice, "issue:7")

    assert (issue["title"], issue["body"]) == ("Slow search", "Body")
    assert (issue["state"], issue["url"]) == (None, None)


def test_another_users_sources_are_invisible(client: TestClient, indexed: dict, new_user: NewUser):
    bob = new_user()
    url = f"/projects/{indexed['project_id']}/sources"

    response = client.get(url, headers=bob, params={"ref": "INC-017"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found", "code": "not_found"}
    assert client.get(url, params={"ref": "INC-017"}).status_code == 401
    # Bob's own project doesn't see Alice's incident either.
    bobs = {"headers": bob, "project_id": new_project(client, bob, "Mine")}
    open_source(client, bobs, "INC-017", status=404)
