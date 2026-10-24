"""The agent graph with a scripted fake model: real tools, real database, no credits."""

import json

import pytest
from sqlalchemy import func, select

from app.agent.events import EventBus
from app.agent.fix import FileChange, FixJudgement, PatchOut, PrOut
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
from app.agent.memory import Lesson, Lessons
from app.llm.router import LLMResult
from app.models import (
    Evidence,
    EvidenceKind,
    Investigation,
    InvestigationStatus,
    Memory,
    TestRun,
    TraceSpan,
)
from app.sandbox import RunResult, SandboxError
from app.tools import ToolContext
from tests.test_retrieve import axis
from tests.test_tools import repo, run_git  # noqa: F401 - pytest fixture


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

    def __init__(self, verdicts, fail_on=None, fix_ok=True, test_path="tests/test_pool.py"):
        self.verdicts, self.fail_on, self.spans, self.seen = list(verdicts), fail_on, [], []
        self.fix_ok, self.test_path = fix_ok, test_path
        self.prompts = {}  # last prompt seen per step

    def model_for(self, tier):
        return f"fake-{tier}"

    async def embed(self, texts, name="embed"):
        return [axis(0) for _ in texts]

    async def complete(self, tier, messages, *, tools=None, schema=None, name="", **kw):
        step = name.split(".")[0]
        self.seen.append(step)
        self.prompts[step] = messages[-1]["content"]
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
            "generate_patch": PatchOut(
                test=FileChange(path=self.test_path, content=POOL_TEST),
                changes=[
                    FileChange(path="config.py", content="DB_POOL_SIZE = 20\nINSTANCES = 5\n")
                ],
                explanation="Restore the pool to 20.",
            ),
            "verify_fix": FixJudgement(addresses_root_cause=self.fix_ok, notes="addresses it"),
            "draft_pr": PrOut(title="fix(db): restore pool size to 20", body="## Root cause"),
            "save_memories": Lessons(
                lessons=[
                    Lesson(
                        category="previous_incident",
                        content="Raising the pool size past the limit causes 500s.",
                    )
                ]
            ),
        }
        parsed = self.verdicts.pop(0) if step == "verdict" else replies[step]
        return answer(name, parsed)


WEAK = VerdictOut(
    verdict="weak", supports=["F1"], contradicts=[], gaps=["check the logs"], notes="?"
)
SUPPORTED = VerdictOut(verdict="supported", supports=["F1", "F2"], contradicts=[], notes="fits")
REFUTED = VerdictOut(verdict="refuted", supports=[], contradicts=["F1"], notes="no")
POOL_TEST = "from config import DB_POOL_SIZE\n\ndef test_pool():\n    assert DB_POOL_SIZE <= 20\n"


class FakeSandbox:
    """Plays a Nebius sandbox: records what was uploaded and returns scripted exit codes."""

    def __init__(self, baseline_exit=1, patched_exit=0, unavailable=False):
        self.exits = [baseline_exit, patched_exit] * 2  # two attempts at most
        self.unavailable, self.runs = unavailable, []

    async def environment(self, repo_dir, setup_command):
        if self.unavailable:
            raise SandboxError("sandboxes are disabled")
        return "tag:fake-env"

    async def run(self, image, workdir, command):
        self.runs.append(
            {
                "command": command,
                "config": (workdir / "config.py").read_text(),
                "has_test": (workdir / "tests" / "test_pool.py").exists(),
            }
        )
        code = self.exits.pop(0)
        return RunResult(
            command=command,
            exit_code=code,
            stdout=f"exit {code}",
            stderr="",
            duration_ms=5,
            operation_id="op-123",
            image=image,
            passed=0 if code else 3,
            failed=1 if code else 0,
        )


async def investigate(db_session, project, repo, model, sandbox=None):  # noqa: F811
    inv = Investigation(project_id=project.id, user_id=project.owner_id, question="Why 500s?")
    db_session.add(inv)
    await db_session.flush()
    bus = EventBus(db_session, inv.id)
    events = []
    bus.listeners.append(events.append)
    ctx = ToolContext(db_session, model, project.id, repo, ref="v2")
    state = await Investigator(inv, ctx, bus, sandbox=sandbox or FakeSandbox()).run()
    return inv, state, events


async def test_full_run_records_only_grounded_facts(db_session, project, repo):  # noqa: F811
    inv, state, events = await investigate(db_session, project, repo, ScriptedModel([SUPPORTED]))
    assert (
        inv.status == InvestigationStatus.AWAITING_APPROVAL
    )  # verified fix: a human approves the PR
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
    scored = next(e.payload for e in events if e.type == "confidence")  # before the fix
    parts = {c.name: c.value for c in scored.components}
    assert parts["coverage"] == pytest.approx(2 / 4)  # code + commit
    assert parts["verifier"] == 1.0 and parts["fix"] == 0.0
    assert scored.score < state["confidence"]  # the verified fix raised it


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
    assert inv.status == InvestigationStatus.AWAITING_APPROVAL
    errors = [e.payload for e in events if e.type == "error"]
    assert errors and not any(e.fatal for e in errors)
    rounds = [
        e.payload.round
        for e in events
        if e.type == "step_started" and e.payload.step == "investigate"
    ]
    assert rounds == [1, 2]  # no facts after round 1, so it looked again before hypothesising


def of(events, kind):
    return [e.payload for e in events if e.type == kind]


async def test_a_verified_fix_goes_red_then_green_and_raises_confidence(db_session, project, repo):  # noqa: F811
    sandbox = FakeSandbox()
    inv, state, events = await investigate(
        db_session, project, repo, ScriptedModel([SUPPORTED]), sandbox
    )
    baseline, patched = sandbox.runs
    assert baseline["has_test"] and "100" in baseline["config"]  # current code + new test
    assert baseline["command"] == "pytest -q tests/test_pool.py"
    assert patched["has_test"] and "= 20" in patched["config"]  # fix applied, whole suite
    assert patched["command"] == "pytest -q"
    assert [t.as_expected for t in of(events, "test_completed")] == [True, True]
    assert of(events, "patch_status")[-1].status == "verified"
    before, after = of(events, "confidence")
    assert {c.name: c.value for c in after.components}["fix"] == 1.0 and after.score > before.score
    assert of(events, "report")[0].fix == "verified" and of(events, "pr_draft")
    assert "-DB_POOL_SIZE = 100" in of(events, "patch_proposed")[0].diff
    runs = await db_session.scalar(select(func.count()).select_from(TestRun))
    assert runs == 2


async def test_a_test_that_does_not_reproduce_the_bug_is_retried_then_reported(
    db_session,
    project,
    repo,  # noqa: F811
):  # noqa: F811
    sandbox = FakeSandbox(baseline_exit=0)  # the new test passes on buggy code: proves nothing
    _, state, events = await investigate(
        db_session, project, repo, ScriptedModel([SUPPORTED]), sandbox
    )
    assert [p.attempt for p in of(events, "patch_proposed")] == [1, 2]
    assert [p.status for p in of(events, "patch_status")] == ["tests_failed", "tests_failed"]
    assert of(events, "report")[0].fix == "failed" and not of(events, "pr_draft")


async def test_a_fix_that_only_games_the_test_is_rejected(db_session, project, repo):  # noqa: F811
    model = ScriptedModel([SUPPORTED], fix_ok=False)
    _, _, events = await investigate(db_session, project, repo, model)
    assert of(events, "patch_status")[-1].status == "rejected"
    assert of(events, "report")[0].fix == "failed"


async def test_without_a_sandbox_the_fix_is_untested_not_fatal(db_session, project, repo):  # noqa: F811
    inv, _, events = await investigate(
        db_session, project, repo, ScriptedModel([SUPPORTED]), FakeSandbox(unavailable=True)
    )
    assert inv.status == InvestigationStatus.COMPLETED
    assert of(events, "patch_status")[-1].status == "untested"
    assert of(events, "report")[0].fix == "untested" and not of(events, "pr_draft")


async def test_an_unverified_root_cause_gets_no_fix(db_session, project, repo):  # noqa: F811
    model = ScriptedModel([WEAK, REFUTED])
    _, _, events = await investigate(db_session, project, repo, model)
    assert not of(events, "patch_proposed")
    assert of(events, "report")[0].fix == "none"


async def test_a_patch_that_edits_existing_tests_never_reaches_the_sandbox(
    db_session,
    project,
    repo,  # noqa: F811
):
    (repo / "tests").mkdir()
    (repo / "tests" / "test_existing.py").write_text("def test_ok():\n    assert True\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-q", "-m", "add a test")
    run_git(repo, "tag", "-f", "v2")
    sandbox = FakeSandbox()
    model = ScriptedModel([SUPPORTED], test_path="tests/test_existing.py")
    _, _, events = await investigate(db_session, project, repo, model, sandbox)
    assert sandbox.runs == []  # rejected before testing, both attempts
    statuses = of(events, "patch_status")
    assert [s.status for s in statuses] == ["rejected", "rejected"]
    assert "tests/test_existing.py" in statuses[0].notes
    assert of(events, "report")[0].fix == "failed"


async def test_a_run_with_a_verified_fix_ends_awaiting_approval(db_session, project, repo):  # noqa: F811
    inv, _, events = await investigate(db_session, project, repo, ScriptedModel([SUPPORTED]))
    assert inv.status == InvestigationStatus.AWAITING_APPROVAL
    assert inv.report["deployed_ref"] == "v2"  # needed later to open the PR
    assert [e.payload.status for e in events if e.type == "status"][-1] == "awaiting_approval"
    assert events[-1].type == "done" and events[-1].payload.status == "completed"  # the run is done


async def test_a_verified_run_saves_a_lesson_and_the_next_run_gets_it_as_a_hint(
    db_session,
    project,
    repo,  # noqa: F811
):
    await investigate(db_session, project, repo, ScriptedModel([SUPPORTED]))
    memories = (await db_session.scalars(select(Memory))).all()
    assert [m.content for m in memories] == ["Raising the pool size past the limit causes 500s."]
    assert memories[0].created_by == "agent" and memories[0].source_investigation_id

    second = ScriptedModel([SUPPORTED])
    _, _, events = await investigate(db_session, project, repo, second)
    assert (
        "HINTS" in second.prompts["plan"] and "pool size past the limit" in second.prompts["plan"]
    )
    # The same lesson is not saved twice.
    assert len((await db_session.scalars(select(Memory))).all()) == 1


async def test_an_unverified_root_cause_teaches_nothing(db_session, project, repo):  # noqa: F811
    await investigate(db_session, project, repo, ScriptedModel([WEAK, REFUTED]))
    assert (await db_session.scalars(select(Memory))).all() == []


class RepeatingModel(ScriptedModel):
    """Calls get_file on config.py twice in a row during the first investigate round."""

    def __init__(self, verdicts):
        super().__init__(verdicts)
        self.investigate_messages = []

    async def complete(self, tier, messages, *, tools=None, schema=None, name="", **kw):
        if tools is not None and name.startswith("investigate"):
            self.investigate_messages = messages
            done = sum(m["role"] == "tool" for m in messages)
            if done < 2:
                self.spans.append(
                    {
                        "name": name,
                        "model": "fake",
                        "tier": tier,
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "cost_usd": 0.0,
                        "latency_ms": 1,
                    }
                )
                call = tool_call(done + 1, "functions.get_file", {"path": "config.py"})
                return answer(name, tool_calls=[call])
            return answer(name)
        return await super().complete(tier, messages, tools=tools, schema=schema, name=name, **kw)


async def test_a_repeated_call_is_refused_and_the_checklist_is_shown(db_session, project, repo):  # noqa: F811
    model = RepeatingModel([SUPPORTED, SUPPORTED])
    _, _, events = await investigate(db_session, project, repo, model)
    first_round = model.investigate_messages
    tool_outputs = [m["content"] for m in first_round if m["role"] == "tool"]
    assert "DB_POOL_SIZE = 100" in tool_outputs[0]  # the first call ran (despite the prefix)
    assert "already made this exact call" in tool_outputs[1]  # the repeat did not
    notes = [m["content"] for m in first_round if m["role"] == "user"][1:]
    assert "tool call(s) left" in notes[0] and "the documented rules" in notes[0]
    assert "the deployed code" not in notes[0]  # get_file was used, so that item is covered


class HealthyModel(ScriptedModel):
    """The hypothesis step finds nothing broken."""

    async def complete(self, tier, messages, *, tools=None, schema=None, name="", **kw):
        if name.startswith("hypothesize"):
            self.spans.append(
                {
                    "name": name,
                    "model": "fake",
                    "tier": tier,
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "cost_usd": 0.0,
                    "latency_ms": 1,
                }
            )
            return answer(
                name,
                Hypotheses(
                    fault_found=False,
                    hypotheses=[
                        HypothesisItem(
                            statement="No fault found: no errors around the reported time.",
                            supporting=["F1"],
                        )
                    ],
                ),
            )
        return await super().complete(tier, messages, tools=tools, schema=schema, name=name, **kw)


async def test_when_nothing_is_broken_nothing_is_claimed_fixed_or_remembered(
    db_session,
    project,
    repo,  # noqa: F811
):
    inv, state, events = await investigate(db_session, project, repo, HealthyModel([SUPPORTED]))
    assert not of(events, "patch_proposed")
    [report] = of(events, "report")
    assert report.root_cause is None and report.fix == "none"
    assert inv.status == InvestigationStatus.COMPLETED  # not awaiting approval: no PR
    assert (await db_session.scalars(select(Memory))).all() == []


class BatchModel(ScriptedModel):
    """Five tool calls, so extraction runs in two batches; the first batch's extraction fails."""

    CALLS = [
        ("search_github_commits", {}),
        ("search_github_commits", {"since": "2026-10-01"}),
        ("get_file", {"path": "README.md"}),
        ("search_logs", {}),
        ("get_file", {"path": "config.py"}),
    ]

    def __init__(self, verdicts):
        super().__init__(verdicts)
        self.extractions = 0

    async def complete(self, tier, messages, *, tools=None, schema=None, name="", **kw):
        step = name.split(".")[0]
        note = {
            "name": name,
            "model": "fake",
            "tier": tier,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "cost_usd": 0.0,
            "latency_ms": 1,
        }
        if tools is not None and step == "investigate":
            self.spans.append(note)
            done = sum(m["role"] == "tool" for m in messages)
            if done < len(self.CALLS) and not any(
                "round 2" in m["content"] for m in messages if m["role"] == "user"
            ):
                name_, args = self.CALLS[done]
                return answer(name, tool_calls=[tool_call(done + 1, name_, args)])
            return answer(name)
        if step == "extract_facts":
            self.spans.append(note)
            self.extractions += 1
            if self.extractions == 1:
                raise ValueError("model emitted blank lines until the token limit")
            return answer(
                name,
                FactList(
                    facts=[
                        FactItem(
                            statement="Pool size is 100",
                            call=5,
                            excerpt="DB_POOL_SIZE = 100",
                            source_ref="config.py L1-2 @ v2",
                        )
                    ]
                ),
            )
        return await super().complete(tier, messages, tools=tools, schema=schema, name=name, **kw)


async def test_a_failed_extraction_batch_loses_only_its_own_facts(db_session, project, repo):  # noqa: F811
    model = BatchModel([SUPPORTED, SUPPORTED])
    _, state, events = await investigate(db_session, project, repo, model)
    assert any("fact extraction failed" in e.payload.message for e in events if e.type == "error")
    first_round = [f for f in state["facts"] if f["statement"] == "Pool size is 100"]
    assert first_round and first_round[0]["label"] == "F1"  # batch 2 still produced its fact
