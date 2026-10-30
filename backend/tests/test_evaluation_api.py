"""The Evaluation page's API: reads what eval/evaluate.py saved (no model calls)."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.models import EvaluationRun


def add_run(started_at: datetime, accuracy: float) -> uuid.UUID:
    run_id = uuid.uuid4()

    async def insert():
        engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)  # the test DB
        async with engine.begin() as conn:
            await conn.execute(
                EvaluationRun.__table__.insert().values(
                    id=run_id,
                    started_at=started_at,
                    git_sha="abc123",
                    model_config={"ultra": "nvidia/Nemotron-3-Ultra-550b-a55b"},
                    metrics={
                        "root_cause_accuracy": accuracy,
                        "cases": 2,
                        "no_false_fix_rate": None,
                    },
                    cases=[
                        {"id": "s1-q1", "root_cause_correct": True},
                        {"id": "s2-q1", "fix": False},
                    ],
                )
            )
        await engine.dispose()

    asyncio.run(insert())
    return run_id


def test_runs_are_listed_newest_first_with_their_cases(client: TestClient, new_user):
    alice = new_user()
    now = datetime.now(UTC)
    older, newer = add_run(now - timedelta(days=1), 0.5), add_run(now, 1.0)

    listed = [r["id"] for r in client.get("/evaluation/runs", headers=alice).json()]
    assert listed.index(str(newer)) < listed.index(str(older))

    detail = client.get(f"/evaluation/runs/{newer}", headers=alice).json()
    assert detail["metrics"]["root_cause_accuracy"] == 1.0
    assert detail["models"]["ultra"].startswith("nvidia/")
    assert [c["id"] for c in detail["cases"]] == ["s1-q1", "s2-q1"]

    assert client.get(f"/evaluation/runs/{uuid.uuid4()}", headers=alice).status_code == 404
    assert client.get("/evaluation/runs").status_code == 401  # needs a login
