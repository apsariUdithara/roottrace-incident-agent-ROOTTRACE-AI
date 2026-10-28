import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentChunk, DocumentSourceType
from app.tools.registry import ToolContext
from app.tools.search import IncidentKey, SearchArgs, get_incident, search_previous_incidents
from tests.support import FakeEmbedder, db, new_project

NewUser = Callable[[], dict[str, str]]

POOL = {
    "key": "INC-017",
    "title": "Payments 500s: connection pool exhausted",
    "severity": "SEV2",
    "symptoms": "500s on /payments, TimeoutError in the pool",
    "root_cause": "Pool size cut from 20 to 5",
    "fix": "Restored the pool size",
    "commit_sha": "a3f9c21",
    "tags": ["db", "pool"],
    "status": "resolved",
    "occurred_at": "2026-03-14T09:30:00Z",
}


def outage() -> openai.APIConnectionError:
    request = httpx2.Request("POST", "https://api.tokenfactory.nebius.com/v1/embeddings")
    return openai.APIConnectionError(request=request)


def unique(text: str) -> str:
    """An embedding is reused for identical text in any project, so a test that counts
    embedding calls needs text that no other test has stored."""
    return f"{text} {uuid.uuid4().hex[:8]}"


def create(client: TestClient, headers: dict[str, str], project_id: str, **fields: Any):
    body = {"key": "INC-017", "title": unique("Pool exhausted"), **fields}
    return client.post(f"/projects/{project_id}/incidents", headers=headers, json=body)


def indexed(project_id: str) -> dict[str, list[str]]:
    """The project's searchable incidents: key -> its chunks' text."""

    async def look(session: AsyncSession) -> dict[str, list[str]]:
        rows = await session.execute(
            select(Document.path, DocumentChunk.content)
            .join(DocumentChunk)
            .where(
                Document.project_id == uuid.UUID(project_id),
                Document.source_type == DocumentSourceType.INCIDENT,
            )
        )
        found: dict[str, list[str]] = {}
        for path, content in rows:
            found.setdefault(path, []).append(content)
        return found

    return db(look)


@pytest.fixture
def alice(client: TestClient, new_user: NewUser) -> dict[str, Any]:
    headers = new_user()
    return {"headers": headers, "project_id": new_project(client, headers)}


def test_create_then_list_and_get(client: TestClient, alice: dict, embedder: FakeEmbedder):
    created = create(client, alice["headers"], alice["project_id"], **POOL)

    assert created.status_code == 201, created.text
    incident = created.json()
    assert incident == {
        **POOL,
        "occurred_at": "2026-03-14T09:30:00Z",
        "id": incident["id"],
        "created_at": incident["created_at"],
    }
    base = f"/projects/{alice['project_id']}/incidents"
    assert client.get(base, headers=alice["headers"]).json() == [incident]
    assert client.get(f"{base}/{incident['id']}", headers=alice["headers"]).json() == incident


def test_defaults(client: TestClient, alice: dict, embedder: FakeEmbedder):
    incident = create(client, alice["headers"], alice["project_id"]).json()

    assert {
        k: incident[k] for k in ("severity", "root_cause", "tags", "status", "occurred_at")
    } == {
        "severity": None,
        "root_cause": None,
        "tags": [],
        "status": "open",
        "occurred_at": None,
    }


def test_a_time_without_a_timezone_is_utc(client: TestClient, alice: dict, embedder: FakeEmbedder):
    incident = create(
        client, alice["headers"], alice["project_id"], occurred_at="2026-03-14T09:30:00"
    ).json()

    assert incident["occurred_at"] == "2026-03-14T09:30:00Z"  # not the server's local time


def test_a_new_incident_is_searchable_by_the_agent(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    title = unique("Payments 500s")
    create(client, alice["headers"], alice["project_id"], **{**POOL, "title": title})

    chunks = indexed(alice["project_id"])
    assert list(chunks) == ["INC-017"]
    # The same text as an ingested record, so seeded and added incidents search alike.
    assert chunks["INC-017"] == [
        "INC-017 · SEV2 · 2026-03-14\n"
        f"{title}\n"
        "Symptoms: 500s on /payments, TimeoutError in the pool\n"
        "Root cause: Pool size cut from 20 to 5\n"
        "Fix: Restored the pool size"
    ]
    assert len(embedder.texts) == 1
    assert "Pool size cut from 20 to 5" in embedder.texts[0]


def test_the_agents_tools_find_an_incident_added_here(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    create(client, alice["headers"], alice["project_id"], key="INC-016", title="Disk full")
    create(client, alice["headers"], alice["project_id"], **POOL)

    async def investigate(session: AsyncSession) -> tuple[dict, dict]:
        tools = ToolContext(session, embedder, uuid.UUID(alice["project_id"]), Path("."))
        found = await search_previous_incidents(tools, SearchArgs(query="connection pool"))
        return found, await get_incident(tools, IncidentKey(key="INC-017"))

    found, full = db(investigate)

    assert found["results"][0]["ref"] == "INC-017"
    assert "Root cause: Pool size cut from 20 to 5" in found["results"][0]["content"]
    assert (full["ref"], full["root_cause"]) == ("INC-017", "Pool size cut from 20 to 5")


def test_keys_look_like_inc_017(client: TestClient, alice: dict, embedder: FakeEmbedder):
    created = create(client, alice["headers"], alice["project_id"], key="  inc-21 ")

    assert created.json()["key"] == "INC-21"
    for key in ("Pool outage", "INC-", "INC 17", "17"):
        response = create(client, alice["headers"], alice["project_id"], key=key)
        assert response.status_code == 422, key


def test_a_key_is_unique_within_its_project(
    client: TestClient, alice: dict, new_user: NewUser, embedder: FakeEmbedder
):
    create(client, alice["headers"], alice["project_id"])

    again = create(client, alice["headers"], alice["project_id"], title="Another")

    assert again.status_code == 409
    assert again.json()["detail"] == "This project already has an incident INC-017"
    assert len(embedder.texts) == 1  # the duplicate cost no embedding call
    other = new_project(client, alice["headers"], "Refunds")
    assert create(client, alice["headers"], other).status_code == 201


def test_list_is_newest_first(client: TestClient, alice: dict, embedder: FakeEmbedder):
    for key, occurred_at in (
        ("INC-1", "2026-01-10T00:00:00Z"),
        ("INC-3", None),  # no date: counts from when it was added, today
        ("INC-2", "2026-02-10T00:00:00Z"),
    ):
        create(client, alice["headers"], alice["project_id"], key=key, occurred_at=occurred_at)

    listed = client.get(f"/projects/{alice['project_id']}/incidents", headers=alice["headers"])

    assert [incident["key"] for incident in listed.json()] == ["INC-3", "INC-2", "INC-1"]


def test_update_changes_only_what_is_sent(client: TestClient, alice: dict, embedder: FakeEmbedder):
    incident = create(client, alice["headers"], alice["project_id"], **POOL).json()
    url = f"/projects/{alice['project_id']}/incidents/{incident['id']}"

    updated = client.patch(
        url, headers=alice["headers"], json={"root_cause": None, "title": None, "tags": ["pool"]}
    )

    assert updated.status_code == 200, updated.text
    # null clears an optional field, and leaves a required one alone
    assert updated.json() == {**incident, "root_cause": None, "tags": ["pool"]}
    assert client.get(url, headers=alice["headers"]).json() == updated.json()
    assert "Root cause: \n" in indexed(alice["project_id"])["INC-017"][0]


def test_only_a_change_to_the_text_is_embedded_again(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    fields = {**POOL, "title": unique("Pool exhausted")}
    incident = create(client, alice["headers"], alice["project_id"], **fields).json()
    url = f"/projects/{alice['project_id']}/incidents/{incident['id']}"

    client.patch(url, headers=alice["headers"], json={"status": "open", "tags": []})
    assert len(embedder.texts) == 1  # status and tags aren't in the searchable text

    client.patch(url, headers=alice["headers"], json={"fix": "Pool size back to 20"})
    assert len(embedder.texts) == 2
    assert indexed(alice["project_id"])["INC-017"][0].endswith("Fix: Pool size back to 20")


def test_a_new_key_moves_it_in_the_index(client: TestClient, alice: dict, embedder: FakeEmbedder):
    incident = create(client, alice["headers"], alice["project_id"], **POOL).json()
    create(client, alice["headers"], alice["project_id"], key="INC-018", title="Retry storm")
    url = f"/projects/{alice['project_id']}/incidents/{incident['id']}"

    taken = client.patch(url, headers=alice["headers"], json={"key": "INC-018"})
    assert taken.status_code == 409
    assert taken.json()["detail"] == "This project already has an incident INC-018"

    moved = client.patch(url, headers=alice["headers"], json={"key": "INC-117"})
    assert moved.json()["key"] == "INC-117"
    assert sorted(indexed(alice["project_id"])) == ["INC-018", "INC-117"]


def test_delete_also_removes_it_from_the_index(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    incident = create(client, alice["headers"], alice["project_id"], **POOL).json()
    create(client, alice["headers"], alice["project_id"], key="INC-018", title="Retry storm")
    url = f"/projects/{alice['project_id']}/incidents/{incident['id']}"

    response = client.delete(url, headers=alice["headers"])

    assert response.status_code == 204
    assert client.get(url, headers=alice["headers"]).status_code == 404
    assert list(indexed(alice["project_id"])) == ["INC-018"]


def test_nothing_is_saved_when_indexing_fails(
    client: TestClient, alice: dict, embedder: FakeEmbedder, caplog: pytest.LogCaptureFixture
):
    incident = create(client, alice["headers"], alice["project_id"], **POOL).json()
    url = f"/projects/{alice['project_id']}/incidents/{incident['id']}"
    embedder.fail = outage()

    with caplog.at_level(logging.WARNING):
        created = create(client, alice["headers"], alice["project_id"], key="INC-018")
        updated = client.patch(url, headers=alice["headers"], json={"fix": "Something else"})

    for response in (created, updated):
        assert response.status_code == 502
        assert response.json() == {
            "detail": "Couldn't index the incident for search, so it wasn't saved. "
            "Please try again in a moment.",
            "code": "bad_gateway",
        }
    listed = client.get(f"/projects/{alice['project_id']}/incidents", headers=alice["headers"])
    assert listed.json() == [incident]  # no INC-018, and the fix unchanged
    assert list(indexed(alice["project_id"])) == ["INC-017"]
    assert "APIConnectionError" in caplog.text


def test_another_users_incidents_are_invisible(
    client: TestClient, alice: dict, new_user: NewUser, embedder: FakeEmbedder
):
    bob = new_user()
    incident = create(client, alice["headers"], alice["project_id"]).json()
    base = f"/projects/{alice['project_id']}/incidents"

    for response in (
        client.get(base, headers=bob),
        create(client, bob, alice["project_id"], key="INC-099"),
        client.get(f"{base}/{incident['id']}", headers=bob),
        client.patch(f"{base}/{incident['id']}", headers=bob, json={"title": "Hijacked"}),
        client.delete(f"{base}/{incident['id']}", headers=bob),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found", "code": "not_found"}

    assert client.get(base, headers=alice["headers"]).json() == [incident]


def test_an_incident_is_only_found_under_its_own_project(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    incident_id = create(client, alice["headers"], alice["project_id"]).json()["id"]
    other = new_project(client, alice["headers"], "Refunds")

    for incident in (incident_id, str(uuid.uuid4())):
        url = f"/projects/{other}/incidents/{incident}"
        for response in (
            client.get(url, headers=alice["headers"]),
            client.patch(url, headers=alice["headers"], json={"title": "Moved"}),
            client.delete(url, headers=alice["headers"]),
        ):
            assert response.status_code == 404
            assert response.json() == {"detail": "Incident not found", "code": "not_found"}


def test_needs_a_login(client: TestClient):
    base = f"/projects/{uuid.uuid4()}/incidents"
    incident = f"{base}/{uuid.uuid4()}"

    for response in (
        client.get(base),
        client.post(base, json={"key": "INC-1", "title": "x"}),
        client.get(incident),
        client.patch(incident, json={"title": "x"}),
        client.delete(incident),
    ):
        assert response.status_code == 401


def test_rejects_bad_input(client: TestClient, alice: dict, embedder: FakeEmbedder):
    for fields in (
        {"title": "  "},
        {"commit_sha": "not-a-sha"},
        {"tags": [f"t{i}" for i in range(21)]},
        {"status": "closed"},
        {"severity": ""},
    ):
        response = create(client, alice["headers"], alice["project_id"], **fields)
        assert response.status_code == 422, fields
        assert response.json()["code"] == "validation_error"

    assert embedder.texts == []
