"""'Try demo', an investigation's trace, and the Dashboard numbers, with no model calls."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import auth as auth_routes
from app.core.config import get_settings
from app.models import Investigation, Patch, TraceSpan


def insert(*rows) -> None:
    """(table, values) pairs, in order, into the test database."""

    async def run():
        engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
        async with engine.begin() as conn:
            for table, values in rows:
                await conn.execute(table.__table__.insert().values(**values))
        await engine.dispose()

    asyncio.run(run())


def setup_investigation(client, headers) -> tuple[str, uuid.UUID]:
    """A project with one finished investigation (90% confident, 120 s, $0.06), two patch
    attempts (one verified) and three spans."""
    project_id = client.post("/projects", headers=headers, json={"name": "Payments"}).json()["id"]
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    inv, start = uuid.uuid4(), datetime.now(UTC) - timedelta(minutes=5)
    span = dict(investigation_id=inv, status="ok")
    insert(
        (
            Investigation,
            dict(
                id=inv,
                project_id=uuid.UUID(project_id),
                user_id=uuid.UUID(user_id),
                question="Why are payments failing?",
                status="completed",
                confidence=0.9,
                total_cost_usd=0.06,
                started_at=start,
                finished_at=start + timedelta(seconds=120),
            ),
        ),
        (
            Patch,
            dict(id=uuid.uuid4(), investigation_id=inv, attempt=1, diff="", status="tests_failed"),
        ),
        (Patch, dict(id=uuid.uuid4(), investigation_id=inv, attempt=2, diff="", status="verified")),
        (
            TraceSpan,
            dict(
                id=uuid.uuid4(),
                kind="llm",
                name="plan",
                tier="super",
                started_at=start,
                latency_ms=2000,
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.01,
                **span,
            ),
        ),
        (
            TraceSpan,
            dict(
                id=uuid.uuid4(),
                kind="tool",
                name="search_logs",
                started_at=start + timedelta(seconds=3),
                latency_ms=500,
                **span,
            ),
        ),
        (
            TraceSpan,
            dict(
                id=uuid.uuid4(),
                kind="llm",
                name="report",
                tier="ultra",
                started_at=start + timedelta(seconds=10),
                latency_ms=5000,
                prompt_tokens=300,
                completion_tokens=200,
                cost_usd=0.02,
                **span,
            ),
        ),
    )
    return project_id, inv


def test_the_trace_lists_spans_in_order_with_totals(client: TestClient, new_user):
    alice, bob = new_user(), new_user()
    _, inv = setup_investigation(client, alice)

    trace = client.get(f"/investigations/{inv}/trace", headers=alice).json()
    assert [s["name"] for s in trace["spans"]] == ["plan", "search_logs", "report"]
    assert trace["totals"] == {
        "spans": 3,
        "llm_calls": 2,
        "prompt_tokens": 400,
        "completion_tokens": 250,
        "cost_usd": 0.03,
        "duration_ms": 15_000,  # first start to the last span's end
    }
    assert client.get(f"/investigations/{inv}/trace", headers=bob).status_code == 404


def test_the_dashboard_averages_finished_runs_and_scopes_to_your_projects(client, new_user):
    alice, bob = new_user(), new_user()
    project_id, inv = setup_investigation(client, alice)

    stats = client.get("/stats/dashboard", headers=alice).json()
    assert stats["investigations"] == 1
    assert stats["avg_confidence"] == 0.9 and stats["avg_duration_ms"] == 120_000
    assert stats["avg_cost_usd"] == 0.06 and stats["patch_success_rate"] == 0.5
    assert [i["id"] for i in stats["recent_investigations"]] == [str(inv)]

    empty = client.get("/stats/dashboard", headers=bob).json()
    assert empty["investigations"] == 0 and empty["avg_confidence"] is None
    assert (
        client.get("/stats/dashboard", headers=bob, params={"project_id": project_id}).status_code
        == 404
    )


def test_try_demo_logs_in_as_the_demo_user_or_says_it_isnt_set_up(client: TestClient, monkeypatch):
    email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
    settings = get_settings().model_copy(update={"demo_email": email})
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)
    assert client.post("/auth/demo").status_code == 503  # nobody has seeded the demo user yet

    client.post("/auth/register", json={"email": email, "password": "correct horse battery"})
    client.cookies.clear()
    demo = client.post("/auth/demo")
    assert demo.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {demo.json()['access_token']}"})
    assert me.json()["email"] == email
