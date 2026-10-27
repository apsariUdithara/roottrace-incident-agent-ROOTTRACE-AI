import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret
from app.models import Document, DocumentChunk, DocumentSourceType, Repository, SandboxStatus
from tests.support import db, new_project

NewUser = Callable[[], dict[str, str]]
URL = "https://github.com/acme/payments"


def stored(repository_id: str) -> Repository:
    return db(lambda session: session.get(Repository, uuid.UUID(repository_id)))


def connect(client: TestClient, headers: dict[str, str], project_id: str, **fields: Any):
    return client.post(
        f"/projects/{project_id}/repositories", headers=headers, json={"url": URL, **fields}
    )


def test_connect_then_list(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = new_project(client, alice)

    created = connect(client, alice, project_id)

    assert created.status_code == 201, created.text
    repository = created.json()
    assert repository == {
        "id": repository["id"],
        "provider": "github",
        "url": URL,
        "default_branch": "main",
        "has_token": False,
        "setup_command": None,  # auto-detect
        "test_command": "pytest -q",
        "sandbox_status": "unknown",
        "ingest_status": "not_indexed",
        "ingest_error": None,
        "last_indexed_sha": None,
        "last_indexed_at": None,
        "created_at": repository["created_at"],
    }
    listed = client.get(f"/projects/{project_id}/repositories", headers=alice)
    assert listed.json() == [repository]


def test_the_token_is_stored_encrypted_and_never_returned(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = new_project(client, alice)

    created = connect(client, alice, project_id, token="  ghp_secret123\n")

    assert created.json()["has_token"] is True
    listed = client.get(f"/projects/{project_id}/repositories", headers=alice)
    for response in (created, listed):
        assert "ghp_secret123" not in response.text
    ciphertext = stored(created.json()["id"]).encrypted_token
    assert ciphertext is not None
    assert "ghp_secret123" not in ciphertext
    assert decrypt_secret(ciphertext) == "ghp_secret123"  # pasted with spaces and a newline


def test_one_repository_per_project(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = new_project(client, alice)
    connect(client, alice, project_id)

    again = connect(client, alice, project_id, url="https://github.com/acme/refunds")

    assert again.status_code == 409
    assert again.json()["detail"] == (
        "This project already has a repository. Disconnect it to connect another one."
    )
    other_project = new_project(client, alice, "Refunds")
    assert connect(client, alice, other_project).status_code == 201


def test_update_changes_only_what_is_sent(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = new_project(client, alice)
    repository = connect(
        client, alice, project_id, token="ghp_old", setup_command="pip install -e ."
    ).json()
    url = f"/projects/{project_id}/repositories/{repository['id']}"

    branch = client.patch(url, headers=alice, json={"default_branch": "release"})

    assert branch.status_code == 200, branch.text
    assert branch.json() == {**repository, "default_branch": "release"}
    assert decrypt_secret(stored(repository["id"]).encrypted_token) == "ghp_old"

    commands = client.patch(
        url, headers=alice, json={"setup_command": None, "test_command": "pytest -q tests"}
    ).json()
    assert (commands["setup_command"], commands["test_command"]) == (None, "pytest -q tests")

    # Required fields sent as null are left alone.
    assert client.patch(url, headers=alice, json={"test_command": None}).json() == commands


def test_update_replaces_or_removes_the_token(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = new_project(client, alice)
    repository = connect(client, alice, project_id, token="ghp_old").json()
    url = f"/projects/{project_id}/repositories/{repository['id']}"

    replaced = client.patch(url, headers=alice, json={"token": "ghp_new"})

    assert replaced.json()["has_token"] is True
    assert "ghp_new" not in replaced.text
    assert decrypt_secret(stored(repository["id"]).encrypted_token) == "ghp_new"
    removed = client.patch(url, headers=alice, json={"token": None})
    assert removed.json()["has_token"] is False
    assert stored(repository["id"]).encrypted_token is None


def test_new_commands_reset_the_sandbox_status(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = new_project(client, alice)
    repository_id = connect(client, alice, project_id).json()["id"]
    url = f"/projects/{project_id}/repositories/{repository_id}"

    async def mark_untestable(session: AsyncSession) -> None:
        (
            await session.get(Repository, uuid.UUID(repository_id))
        ).sandbox_status = SandboxStatus.UNTESTABLE

    db(mark_untestable)
    unchanged = client.patch(url, headers=alice, json={"test_command": "pytest -q"})
    assert unchanged.json()["sandbox_status"] == "untestable"  # same command: still known

    changed = client.patch(url, headers=alice, json={"setup_command": "pip install -r dev.txt"})
    assert changed.json()["sandbox_status"] == "unknown"  # to be checked again


def add_documents(project_id: str) -> None:
    """A doc and a code file from the repository, and a past incident from elsewhere."""

    async def add(session: AsyncSession) -> None:
        for source_type, path in (
            (DocumentSourceType.DOC, "README.md"),
            (DocumentSourceType.CODE, "app/pool.py"),
            (DocumentSourceType.INCIDENT, "INC-017"),
        ):
            document = Document(
                project_id=uuid.UUID(project_id), source_type=source_type, path=path, hash="h"
            )
            session.add(document)
            await session.flush()
            session.add(
                DocumentChunk(document_id=document.id, project_id=document.project_id, content=path)
            )

    db(add)


def indexed_paths(project_id: str) -> tuple[list[str], int]:
    async def look(session: AsyncSession) -> tuple[list[str], int]:
        project = uuid.UUID(project_id)
        paths = await session.scalars(select(Document.path).where(Document.project_id == project))
        chunks = await session.scalar(
            select(func.count()).where(DocumentChunk.project_id == project)
        )
        return sorted(paths), chunks

    return db(look)


def test_disconnect_removes_what_was_indexed_from_it(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = new_project(client, alice)
    repository_id = connect(client, alice, project_id).json()["id"]
    add_documents(project_id)

    response = client.delete(f"/projects/{project_id}/repositories/{repository_id}", headers=alice)

    assert response.status_code == 204
    assert client.get(f"/projects/{project_id}/repositories", headers=alice).json() == []
    assert indexed_paths(project_id) == (["INC-017"], 1)  # the incident stays
    assert connect(client, alice, project_id).status_code == 201  # and another can be connected


def test_another_users_repository_is_invisible(client: TestClient, new_user: NewUser):
    alice, bob = new_user(), new_user()
    project_id = new_project(client, alice)
    repository_id = connect(client, alice, project_id, token="ghp_secret").json()["id"]
    base = f"/projects/{project_id}/repositories"

    for response in (
        client.get(base, headers=bob),
        client.post(base, headers=bob, json={"url": URL}),
        client.patch(f"{base}/{repository_id}", headers=bob, json={"token": "ghp_bob"}),
        client.delete(f"{base}/{repository_id}", headers=bob),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found", "code": "not_found"}

    assert decrypt_secret(stored(repository_id).encrypted_token) == "ghp_secret"


def test_a_repository_is_only_found_under_its_own_project(client: TestClient, new_user: NewUser):
    alice = new_user()
    payments, refunds = new_project(client, alice), new_project(client, alice, "Refunds")
    repository_id = connect(client, alice, payments).json()["id"]

    for repository in (repository_id, str(uuid.uuid4())):
        url = f"/projects/{refunds}/repositories/{repository}"
        for response in (
            client.patch(url, headers=alice, json={"default_branch": "dev"}),
            client.delete(url, headers=alice),
        ):
            assert response.status_code == 404
            assert response.json() == {"detail": "Repository not found", "code": "not_found"}

    assert stored(repository_id).default_branch == "main"


def test_needs_a_login(client: TestClient):
    base = f"/projects/{uuid.uuid4()}/repositories"
    repository = f"{base}/{uuid.uuid4()}"

    for response in (
        client.get(base),
        client.post(base, json={"url": URL}),
        client.patch(repository, json={"test_command": "pytest"}),
        client.delete(repository),
    ):
        assert response.status_code == 401


def test_rejects_bad_input(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = new_project(client, alice)

    for fields in (
        {"url": "https://gitlab.com/acme/payments"},
        {"url": "https://github.com/acme"},
        {"token": "   "},
        {"test_command": ""},
        {"provider": "gitlab"},
    ):
        response = connect(client, alice, project_id, **fields)
        assert response.status_code == 422, fields
        assert response.json()["code"] == "validation_error"

    assert client.get(f"/projects/{project_id}/repositories", headers=alice).json() == []
