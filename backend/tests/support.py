"""Helpers shared by API tests that also look at the database directly."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

# The PayStream logs of 6 Oct, the day of the hero scenario.
REAL_LOGS = Path(__file__).resolve().parents[2] / "demo-target/fixtures/logs/2026-10-06.jsonl"


def db[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run `work` in its own committed session, outside the app (to look at or set up rows)."""
    from app.core.db import engine as app_engine  # points at the test database

    async def run() -> T:
        engine = create_async_engine(app_engine.url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(run())


class FakeEmbedder:
    """Stands in for the model router: records what's embedded, and can fail like Nebius."""

    def __init__(self):
        self.texts: list[str] = []
        self.fail: Exception | None = None

    async def embed(self, texts, name="embed"):
        if self.fail:
            raise self.fail
        self.texts += texts
        return [[0.1] * 1024 for _ in texts]


def new_project(client: TestClient, headers: dict[str, str], name: str = "Payments") -> str:
    return client.post("/projects", headers=headers, json={"name": name}).json()["id"]
