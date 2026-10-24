from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from app.core.db import get_session
from app.main import app

DB_HINT = "Is Postgres running? docker compose -f infra/docker-compose.yml up -d --wait"


def test_ready_when_database_answers() -> None:
    with TestClient(app) as client:  # runs the lifespan, so the engine pool is disposed after
        response = client.get("/health/ready")

    assert response.status_code == 200, DB_HINT
    assert response.json() == {"status": "ok", "database": "ok"}


class _UnreachableSession:
    async def execute(self, *_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("connection refused")


async def _unreachable_session() -> AsyncIterator[_UnreachableSession]:
    yield _UnreachableSession()


def test_ready_reports_unreachable_database() -> None:
    app.dependency_overrides[get_session] = _unreachable_session
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unreachable"}
