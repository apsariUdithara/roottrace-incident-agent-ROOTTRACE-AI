"""Memories (view, correct, archive, delete) and the follow-up chat history, with no model calls."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import memories as routes
from app.models import Investigation, Memory, Message



def insert(table, **values) -> None:
    async def run():
        engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)  # the test DB
        async with engine.begin() as conn:
            await conn.execute(table.__table__.insert().values(id=uuid.uuid4(), **values))
        await engine.dispose()

    asyncio.run(run())


def test_memories_can_be_listed_corrected_archived_and_deleted(client: TestClient, new_user, monkeypatch):
    alice, bob = new_user(), new_user()
    project_id = client.post("/projects", headers=alice, json={"name": "Payments"}).json()["id"]
    insert(Memory, project_id=uuid.UUID(project_id), category="project_fact",
           content="Payments use PostgreSQL", created_by="agent")
    url = f"/projects/{project_id}/memories"

    [memory] = client.get(url, headers=alice).json()
    assert memory["created_by"] == "agent"
    assert client.get(url, headers=bob).status_code == 404  # another user's project

    async def fake_embed(texts, name="embed"):
        return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr(routes, "get_router", lambda: SimpleNamespace(embed=fake_embed))
    fixed = client.patch(f"{url}/{memory['id']}", headers=alice,
                         json={"content": "Payments use PostgreSQL 16", "status": "archived"}).json()
    assert (fixed["content"], fixed["status"], fixed["created_by"]) == (
        "Payments use PostgreSQL 16", "archived", "user")
    assert client.get(url, headers=alice, params={"status": "active"}).json() == []

    assert client.delete(f"{url}/{memory['id']}", headers=alice).status_code == 204
    assert client.get(url, headers=alice).json() == []
    assert client.delete(f"{url}/{memory['id']}", headers=alice).status_code == 404


def test_the_follow_up_chat_is_listed_oldest_first(client: TestClient, new_user):
    alice = new_user()
    project_id = client.post("/projects", headers=alice, json={"name": "Payments"}).json()["id"]
    user_id = client.get("/auth/me", headers=alice).json()["id"]
    inv_id = uuid.uuid4()

    async def add_investigation():
        engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(Investigation.__table__.insert().values(
                id=inv_id, project_id=uuid.UUID(project_id), user_id=uuid.UUID(user_id),
                question="Why are payments failing?"))
        await engine.dispose()

    asyncio.run(add_investigation())
    now = datetime.now(UTC)
    insert(Message, investigation_id=inv_id, role="assistant", content="Commit a3f9c21 [F1]",
           created_at=now + timedelta(microseconds=1))
    insert(Message, investigation_id=inv_id, role="user", content="Which commit?", created_at=now)

    messages = client.get(f"/investigations/{inv_id}/messages", headers=alice).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
