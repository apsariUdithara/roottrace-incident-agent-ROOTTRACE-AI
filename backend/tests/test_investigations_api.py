"""The investigation routes, with a fake agent run (no model calls)."""

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.agent import run
from app.agent.events import EventBus
from app.api.routes import investigations as routes
from app.core.config import Settings
from app.core.db import SessionLocal
from app.models import Investigation, InvestigationStatus, Repository, Verdict
from app.schemas.events import Done, ReportReady, StatusChanged, Step, StepStarted

# What the agent stores in investigations.report: the report event's payload plus the version.
REPORT = {
    **ReportReady(
        root_cause="pool too big",
        summary="DB_POOL_SIZE x instances exceeds max_connections [F1]",
        verdict=Verdict.SUPPORTED,
        fix="none",
    ).model_dump(mode="json"),
    "deployed_ref": "v1.4.8",
}


async def fake_run(investigation_id, ref=None, reported_at=None):
    """Plays a short agent run: a few events, a report, then done."""
    async with SessionLocal() as session:
        bus = await EventBus.resume(session, investigation_id)
        await bus.publish(StatusChanged(status=InvestigationStatus.RUNNING))
        await bus.publish(StepStarted(step=Step.TRIAGE, tier="nano", model="fake"))
        await session.execute(
            update(Investigation)
            .where(Investigation.id == investigation_id)
            .values(
                status=InvestigationStatus.COMPLETED,
                report=REPORT,
                root_cause="pool too big",
            )
        )
        await bus.publish(
            Done(
                status=InvestigationStatus.COMPLETED,
                duration_ms=5,
                total_tokens=10,
                total_cost_usd=0.0,
            )
        )


@pytest.fixture(autouse=True)
def fake_agent(monkeypatch):
    monkeypatch.setattr(run, "run_existing", fake_run)


def add_repository(project_id: str) -> None:
    async def insert():
        engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)  # the test DB
        async with engine.begin() as conn:
            await conn.execute(
                Repository.__table__.insert().values(
                    id=uuid.uuid4(),
                    project_id=uuid.UUID(project_id),
                    url="https://github.com/acme/demo",
                    local_path="/tmp/demo",
                )
            )
        await engine.dispose()

    asyncio.run(insert())


@pytest.fixture
def started(client: TestClient, new_user):
    alice = new_user()
    project_id = client.post("/projects", headers=alice, json={"name": "Payments"}).json()["id"]
    add_repository(project_id)
    resp = client.post(
        f"/projects/{project_id}/investigations",
        headers=alice,
        json={"question": "Why are payments failing?", "ref": "v1.4.8"},
    )
    return alice, project_id, resp


def read_stream(client, url, headers) -> list[str]:
    with client.stream("GET", url, headers=headers) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        return [line for line in resp.iter_lines() if line.startswith("event: ")]


def test_starting_returns_at_once_and_the_stream_follows_to_done(client, started):
    alice, project_id, resp = started
    assert resp.status_code == 202 and resp.json()["status"] == "queued"
    inv_id = resp.json()["id"]
    events = read_stream(client, f"/investigations/{inv_id}/stream", alice)
    assert events == ["event: status", "event: step_started", "event: done"]
    assert client.get(f"/investigations/{inv_id}", headers=alice).json()["status"] == "completed"
    listed = client.get(f"/projects/{project_id}/investigations", headers=alice).json()
    assert [i["id"] for i in listed] == [inv_id]


def test_a_reconnect_resumes_after_the_last_event_seen(client, started):
    alice, _, resp = started
    inv_id = resp.json()["id"]
    read_stream(client, f"/investigations/{inv_id}/stream", alice)  # let the run finish
    resumed = read_stream(
        client, f"/investigations/{inv_id}/stream", {**alice, "Last-Event-ID": "2"}
    )
    assert resumed == ["event: done"]
    replay = client.get(f"/investigations/{inv_id}/events?after=1", headers=alice).json()
    assert [e["seq"] for e in replay] == [2, 3]


def test_another_users_investigation_is_invisible(client, new_user, started):
    _, _, resp = started
    inv_id, bob = resp.json()["id"], new_user()
    for method, path in [("get", ""), ("get", "/events"), ("get", "/stream"), ("post", "/approve")]:
        assert (
            client.request(method, f"/investigations/{inv_id}{path}", headers=bob).status_code
            == 404
        )


def test_a_project_without_a_repository_cannot_start(client: TestClient, new_user):
    alice = new_user()
    project_id = client.post("/projects", headers=alice, json={"name": "Empty"}).json()["id"]
    resp = client.post(
        f"/projects/{project_id}/investigations",
        headers=alice,
        json={"question": "Why is it down?"},
    )
    assert resp.status_code == 409


def test_follow_up_questions_need_a_report_and_are_answered(client, started, monkeypatch):
    alice, _, resp = started
    inv_id = resp.json()["id"]
    read_stream(client, f"/investigations/{inv_id}/stream", alice)  # run finished: has a report

    async def fake_answer(session, router, investigation_id, question):
        return SimpleNamespace(
            id=uuid.uuid4(),
            role=SimpleNamespace(value="assistant"),
            content=f"Answer to: {question} [F1]",
        )

    monkeypatch.setattr(routes.followup, "answer", fake_answer)
    monkeypatch.setattr(routes, "get_router", lambda: None)  # no real model client (CI has no key)
    reply = client.post(
        f"/investigations/{inv_id}/messages", headers=alice, json={"content": "Which commit?"}
    )
    assert reply.status_code == 200 and reply.json()["content"].endswith("[F1]")


def test_approval_needs_a_github_token_and_a_fix(client, started, monkeypatch):
    alice, _, resp = started
    inv_id = resp.json()["id"]
    monkeypatch.setattr(routes, "get_settings", lambda: Settings(_env_file=None))
    assert client.post(f"/investigations/{inv_id}/approve", headers=alice).status_code == 503
    monkeypatch.setattr(routes, "get_settings", lambda: Settings(_env_file=None, github_token="t"))
    no_fix = client.post(f"/investigations/{inv_id}/approve", headers=alice)
    assert no_fix.status_code == 409 and "no fix" in no_fix.json()["detail"]


def limits(monkeypatch, **overrides):
    monkeypatch.setattr(routes, "get_settings", lambda: Settings(_env_file=None, **overrides))


def start(client, headers, project_id):
    return client.post(
        f"/projects/{project_id}/investigations",
        headers=headers,
        json={"question": "Why are payments failing?"},
    )


def test_only_one_investigation_runs_at_a_time_per_user(client, new_user, monkeypatch):
    async def never_finishes(*args, **kwargs):
        pass  # leaves the investigation queued, as if still running

    monkeypatch.setattr(run, "run_existing", never_finishes)
    alice = new_user()
    project_id = client.post("/projects", headers=alice, json={"name": "Payments"}).json()["id"]
    add_repository(project_id)
    assert start(client, alice, project_id).status_code == 202
    blocked = start(client, alice, project_id)
    assert (
        blocked.status_code == 429 and "already have an investigation" in blocked.json()["detail"]
    )


def test_a_user_gets_a_few_investigations_per_hour(client, started, monkeypatch):
    alice, project_id, first = started
    limits(monkeypatch, investigations_per_hour=2)
    read_stream(client, f"/investigations/{first.json()['id']}/stream", alice)
    second = start(client, alice, project_id)
    read_stream(client, f"/investigations/{second.json()['id']}/stream", alice)
    third = start(client, alice, project_id)
    assert second.status_code == 202
    assert third.status_code == 429 and "2 investigations per hour" in third.json()["detail"]


def test_the_daily_budget_stops_new_investigations(client: TestClient, new_user, monkeypatch):
    alice = new_user()
    project_id = client.post("/projects", headers=alice, json={"name": "Payments"}).json()["id"]
    add_repository(project_id)
    limits(monkeypatch, daily_budget_usd=0.10, investigation_cost_cap_usd=0.50)
    resp = start(client, alice, project_id)
    assert resp.status_code == 429 and "daily budget" in resp.json()["detail"]
