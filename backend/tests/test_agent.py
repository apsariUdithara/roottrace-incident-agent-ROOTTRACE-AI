"""The agent graph with a scripted fake model: real tools, real database, no credits."""

import json

import pytest
from sqlalchemy import func, select

from app.agent.events import EventBus
from app.agent.graph import (
    Comparison,
    FactItem,
    FactList,
    Hypotheses,
    HypothesisItem,
    InferenceItem,
    Investigator,
    Plan,
    ReportOut,
    Triage,
    VerdictOut,
)
from app.llm.router import LLMResult
from app.models import Evidence, EvidenceKind, Investigation, InvestigationStatus, TraceSpan
from app.tools import ToolContext
from tests.test_retrieve import axis
from tests.test_tools import repo  # noqa: F401 - pytest fixture


def answer(name: str, parsed=None, tool_calls=()):
    return LLMResult(
        content="",
        tool_calls=list(tool_calls),
        model="fake",
        tier="super",
        prompt_tokens=100,
        completion_tokens=10,
        cost_usd=0.001,
        latency_ms=1,
        parsed=parsed,
    )


def tool_call(n: int, name: str, args: dict) -> dict:
    return {"id": f"c{n}", "name": name, "arguments": json.dumps(args)}


class ScriptedModel:
    """Answers each step from a script. `verdicts` is consumed one per verify round."""

    def __init__(self, verdicts, fail_on=None):
        self.verdicts, self.fail_on, self.spans, self.seen = list(verdicts), fail_on, [], []

    def model_for(self, tier):
        return f"fake-{tier}"

    async def embed(self, texts, name="embed"):
        return [axis(0) for _ in texts]

    async def complete(self, tier, messages, *, tools=None, schema=None, name="", **kw):
        step = name.split(".")[0]
        self.seen.append(step)
        self.spans.append(
            {
                "name": name,
                "model": "fake",
                "tier": tier,
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "cost_usd": 0.001,
                "latency_ms": 1,
            }
        )
        if step == self.fail_on:
            raise RuntimeError("model unavailable")
        if tools is not None:  # a tool loop
            done = sum(m["role"] == "tool" for m in messages)
            first_round = step == "investigate" and not any(
                "round 2" in m["content"] for m in messages if m["role"] == "user"
            )
            script = (
                [
                    tool_call(1, "get_file", {"path": "config.py"}),
                    tool_call(2, "search_github_commits", {}),
                ]
                if first_round
                else []
            )
            return answer(name, tool_calls=script[done : done + 1])
        replies = {
            "triage": Triage(
                summary="Payments failing after deploy",
                affected="/payments",
                symptoms=["500s"],
                search_terms=["pool"],
            ),
            "plan": Plan(steps=["Read the config", "List recent commits"]),
            "extract_facts": FactList(
                facts=[
                    FactItem(
                        statement="Pool size is 100",
                        call=1,
                        excerpt="DB_POOL_SIZE = 100",
                        source_ref="config.py L1-2 @ v2",
                    ),
                    FactItem(
                        statement="A commit tuned for peak traffic",
                        call=2,
                        excerpt="perf: tune for peak traffic",
                        source_ref="v2",
                    ),
                    # Invented: this text is not in any tool result, so it must be discarded.
                    FactItem(
                        statement="Server allows 500",
                        call=1,
                        excerpt="max_connections = 500",
                        source_ref="config.py",
                    ),
                ]
            ),
            "compare": Comparison(
                inferences=[
                    InferenceItem(statement="The commit raised the pool", cites=["F1", "F2"]),
                    InferenceItem(statement="Cites nothing real", cites=["F99"]),
                ]
            ),
            "hypothesize": Hypotheses(
                hypotheses=[
                    HypothesisItem(
                        statement="Pool raised to 100 exceeds the limit", supporting=["F1"]
                    )
                ]
            ),
            "report": ReportOut(
                root_cause="Pool size raised to 100",
                summary="Pool rose [F1].",
                next_steps=["Restore 20"],
            ),
        }
        parsed = self.verdicts.pop(0) if step == "verdict" else replies[step]
        return answer(name, parsed)


WEAK = VerdictOut(
    verdict="weak", supports=["F1"], contradicts=[], gaps=["check the logs"], notes="?"
)
SUPPORTED = VerdictOut(verdict="supported", supports=["F1", "F2"], contradicts=[], notes="fits")


async def investigate(db_session, project, repo, model):  # noqa: F811
    inv = Investigation(project_id=project.id, user_id=project.owner_id, question="Why 500s?")
    db_session.add(inv)
    await db_session.flush()
    bus = EventBus(db_session, inv.id)
    events = []
    bus.listeners.append(events.append)
    ctx = ToolContext(db_session, model, project.id, repo, ref="v2")
    state = await Investigator(inv, ctx, bus).run()
    return inv, state, events


async def test_full_run_records_only_grounded_facts(db_session, project, repo):  # noqa: F811
    inv, state, events = await investigate(db_session, project, repo, ScriptedModel([SUPPORTED]))
    assert inv.status == InvestigationStatus.COMPLETED
    assert [f["label"] for f in state["facts"]] == ["F1", "F2"]  # the invented fact is gone
    facts = await db_session.scalar(
        select(func.count())
        .select_from(Evidence)
        .where(Evidence.investigation_id == inv.id, Evidence.kind == EvidenceKind.FACT)
    )
    assert facts == 2
    assert [i["label"] for i in state["inferences"]] == ["I1"]  # the uncited one is gone
    assert state["report"]["root_cause"] == "Pool size raised to 100"


async def test_a_weak_verdict_sends_the_agent_back_for_a_second_round(db_session, project, repo):  # noqa: F811
    model = ScriptedModel([WEAK, SUPPORTED])
    _, state, events = await investigate(db_session, project, repo, model)
    rounds = [
        e.payload.round
        for e in events
        if e.type == "step_started" and e.payload.step == "investigate"
    ]
    assert rounds == [1, 2]
    assert [e.payload.reinvestigating for e in events if e.type == "verdict"] == [True, False]
    assert state["verdict"]["verdict"] == "supported"


async def test_events_are_numbered_stored_and_end_with_done(db_session, project, repo):  # noqa: F811
    inv, _, events = await investigate(db_session, project, repo, ScriptedModel([SUPPORTED]))
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert events[-1].type == "done" and events[-1].payload.status == "completed"
    types = [e.type for e in events]
    assert types.index("tool_call") < types.index("evidence_added") < types.index("hypothesis")
    assert types.index("confidence") < types.index("report")
    spans = await db_session.scalar(
        select(func.count()).select_from(TraceSpan).where(TraceSpan.investigation_id == inv.id)
    )
    assert spans > 0


async def test_confidence_comes_from_the_evidence(db_session, project, repo):  # noqa: F811
    _, state, events = await investigate(db_session, project, repo, ScriptedModel([SUPPORTED]))
    [scored] = [e.payload for e in events if e.type == "confidence"]
    parts = {c.name: c.value for c in scored.components}
    assert parts["coverage"] == pytest.approx(2 / 4)  # code + commit
    assert parts["verifier"] == 1.0 and parts["fix"] == 0.0
    assert scored.score == state["confidence"]


async def test_a_failure_is_reported_and_the_run_marked_failed(db_session, project, repo):  # noqa: F811
    inv, state, events = await investigate(
        db_session, project, repo, ScriptedModel([SUPPORTED], fail_on="hypothesize")
    )
    assert state is None and inv.status == InvestigationStatus.FAILED
    assert [e.type for e in events][-3:] == ["error", "status", "done"]
    assert "model unavailable" in events[-3].payload.message


async def test_failed_fact_extraction_is_not_fatal_and_triggers_another_round(
    db_session,
    project,
    repo,  # noqa: F811
):
    model = ScriptedModel([SUPPORTED], fail_on="extract_facts")
    inv, _, events = await investigate(db_session, project, repo, model)
    assert inv.status == InvestigationStatus.COMPLETED
    errors = [e.payload for e in events if e.type == "error"]
    assert errors and not any(e.fatal for e in errors)
    rounds = [
        e.payload.round
        for e in events
        if e.type == "step_started" and e.payload.step == "investigate"
    ]
    assert rounds == [1, 2]  # no facts after round 1, so it looked again before hypothesising
