"""The hand-written scenario-1 replay (app/demo/scenario1.py) must look like a real run."""

import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import investigations as investigations_route
from app.core.config import get_settings
from app.demo import scenario1
from app.models import InvestigationEvent, Patch, Project
from app.rag.ingest import ingest_logs
from app.schemas.events import Step, event_adapter
from app.tools import TOOLS, ToolContext, call_tool
from tests.support import REAL_LOGS, db, new_project

NewUser = Callable[[], dict[str, str]]
EVENTS = scenario1.events()
RUN_ORDER = [  # app/agent/graph.py, for a supported cause with a verified fix
    Step.TRIAGE,
    Step.RECALL_MEMORY,
    Step.PLAN,
    Step.INVESTIGATE,
    Step.COMPARE_EVIDENCE,
    Step.HYPOTHESIZE,
    Step.VERIFY,
    Step.SCORE_CONFIDENCE,
    Step.GENERATE_PATCH,
    Step.RUN_TESTS,
    Step.VERIFY_FIX,
    Step.WRITE_REPORT,
    Step.DRAFT_PR,
    Step.SAVE_MEMORIES,
]


def of_type(kind: str) -> list:
    return [event.payload for event in EVENTS if event.type == kind]


def test_every_event_fits_the_contract_in_order():
    for event in EVENTS:
        wire = event.model_dump(mode="json")
        assert event_adapter.validate_python(wire).model_dump(mode="json") == wire
    assert [event.seq for event in EVENTS] == list(range(1, len(EVENTS) + 1))
    assert all(a.ts <= b.ts for a, b in zip(EVENTS, EVENTS[1:], strict=False))


def test_it_runs_the_agents_steps_in_order():
    started = [payload.step for payload in of_type("step_started")]
    assert started == RUN_ORDER
    open_step = None
    for event in EVENTS:  # each step closes before the next opens, and says how long it took
        if event.type == "step_started":
            assert open_step is None
            open_step = event
        elif event.type == "step_completed":
            assert open_step and event.payload.step == open_step.payload.step
            took = (event.ts - open_step.ts).total_seconds() * 1000
            assert abs(event.payload.duration_ms - took) <= 1
            open_step = None
    assert open_step is None


def test_it_calls_real_tools_with_valid_arguments():
    results = {payload.call_id: payload for payload in of_type("tool_result")}
    for call in of_type("tool_call"):
        TOOLS[call.tool].args.model_validate(call.arguments)  # what the agent would have sent
        assert results[call.call_id].tool == call.tool
    assert len(results) == len(of_type("tool_call"))


async def test_its_log_searches_show_what_the_real_logs_hold(db_session: AsyncSession, project):
    rows = [json.loads(row) for row in REAL_LOGS.read_text(encoding="utf-8").splitlines()]
    await ingest_logs(db_session, project.id, rows)
    context = ToolContext(db_session, None, project.id, Path("."))
    calls = {payload.call_id: payload for payload in of_type("tool_call")}
    searches = [result for result in of_type("tool_result") if result.tool == "search_logs"]

    for shown in searches:
        claimed = json.loads(shown.output_preview)  # abbreviated, but true as far as it goes
        real = json.loads(await call_tool(context, "search_logs", calls[shown.call_id].arguments))
        assert claimed["total"] == real["total"], calls[shown.call_id].arguments
        assert shown.summary.startswith(f"{real['total']} log lines")
        assert claimed["by_level"] == real["by_level"]
        refs = {item["ref"] for item in real["deploys"] + real["samples"]}
        assert {item["ref"] for item in claimed["deploys"] + claimed["samples"]} <= refs
    log_facts = [e for e in of_type("evidence_added") if e.source_type == "log"]
    assert searches and log_facts
    for fact in log_facts:  # each cites a line the agent was shown
        assert any(fact.source_ref in result.output_preview for result in searches)


def test_the_report_cites_only_evidence_that_exists():
    evidence = {payload.label: payload for payload in of_type("evidence_added")}
    report = of_type("report")[0]
    cited = set(re.findall(r"\[([FI]\d+)\]", report.summary))
    assert cited and cited <= set(evidence)
    facts = [e.evidence_id for e in evidence.values() if e.kind == "fact"]
    inferences = [e for e in evidence.values() if e.kind == "inference"]
    assert report.fact_ids == facts
    assert report.inference_ids == [i.evidence_id for i in inferences]
    assert all(set(i.cites) <= set(facts) for i in inferences)
    assert set(report.contradiction_ids) <= set(facts)
    assert report.patch_id == of_type("patch_proposed")[0].patch_id
    assert report.confidence == of_type("confidence")[-1].score  # the score after the fix


def test_its_tests_go_red_then_green_without_claiming_a_sandbox():
    baseline, patched = of_type("test_completed")
    assert (baseline.phase, baseline.exit_code, baseline.failed) == ("baseline", 1, 1)
    assert (patched.phase, patched.exit_code, patched.passed) == ("patched", 0, 15)
    assert baseline.sandbox_image_id is None and patched.sandbox_image_id is None


def test_it_ends_waiting_for_approval():
    *_, status, done = EVENTS
    assert (status.type, status.payload.status) == ("status", "awaiting_approval")
    assert (done.type, done.payload.status) == ("done", "completed")
    # Measured just before `done` is published, as the agent does.
    elapsed_ms = (done.ts - EVENTS[0].ts).total_seconds() * 1000
    assert 0 <= elapsed_ms - done.payload.duration_ms < 50


def test_the_frontend_fixture_is_up_to_date():
    from scripts.seed_fake_investigation import FIXTURE, events_json

    assert FIXTURE.read_text(encoding="utf-8") == events_json(), (
        "Regenerate it: uv run python scripts/seed_fake_investigation.py --json"
    )


@pytest.fixture
def seeded(client: TestClient, new_user: NewUser) -> dict[str, str]:
    headers = new_user()
    project_id = new_project(client, headers, "PayStream")

    async def seed(session: AsyncSession) -> None:
        await scenario1.seed(session, await session.get(Project, uuid.UUID(project_id)))

    db(seed)
    return headers


def test_once_seeded_the_api_serves_it_like_a_real_run(client: TestClient, seeded: dict):
    url = f"/investigations/{scenario1.INVESTIGATION_ID}"

    investigation = client.get(url, headers=seeded).json()
    events = client.get(f"{url}/events", headers=seeded).json()
    with client.stream("GET", f"{url}/stream", headers=seeded) as stream:
        streamed = [line for line in stream.iter_lines() if line.startswith("event: ")]

    assert investigation["status"] == "awaiting_approval"
    assert investigation["question"] == scenario1.QUESTION
    assert investigation["report"]["deployed_ref"] == "v1.4.8"
    assert investigation["total_tokens"] == scenario1.TOTAL_TOKENS
    assert events == [event.model_dump(mode="json") for event in EVENTS]
    assert len(streamed) == len(EVENTS) and streamed[-1] == "event: done"


def test_seeding_again_replaces_it(client: TestClient, seeded: dict, new_user: NewUser):
    headers = new_user()
    other = new_project(client, headers, "PayStream")

    async def seed_and_count(session: AsyncSession) -> int:
        await scenario1.seed(session, await session.get(Project, uuid.UUID(other)))
        return await session.scalar(
            select(func.count()).where(
                InvestigationEvent.investigation_id == scenario1.INVESTIGATION_ID
            )
        )

    assert db(seed_and_count) == len(EVENTS)
    url = f"/investigations/{scenario1.INVESTIGATION_ID}"
    assert client.get(url, headers=seeded).status_code == 404  # moved to the other project
    assert client.get(url, headers=headers).status_code == 200


def test_it_can_never_be_approved_into_a_pull_request(
    client: TestClient, seeded: dict, monkeypatch: pytest.MonkeyPatch
):
    settings = get_settings().model_copy(update={"github_token": "ghp_would_be_used"})
    monkeypatch.setattr(investigations_route, "get_settings", lambda: settings)

    async def patches(session: AsyncSession) -> int:
        query = select(func.count()).where(Patch.investigation_id == scenario1.INVESTIGATION_ID)
        return await session.scalar(query)

    response = client.post(f"/investigations/{scenario1.INVESTIGATION_ID}/approve", headers=seeded)

    assert db(patches) == 0
    assert response.status_code == 409
    assert response.json()["detail"] == "This investigation has no fix to approve"
