"""A hand-written replay of the hero scenario: PayStream v1.4.8, payment API 500s after a deploy.

For building and testing the Investigation page without credits or a sandbox. It publishes what
a real run publishes, in the same order (app/agent/graph.py), and every event is built from the
real payload classes, so it can't drift from the contract without a test failing.

It's grounded in real data:
- the logs of 6 Oct (demo-target/fixtures/logs/2026-10-06.jsonl): 94 OperationalError 500s on
  /payments from 14:26 to 15:19 on all 5 instances, none before; deploy at 14:20, done 14:30;
- the bad commit and files (demo-target/scripts/scenarios.py), docs/database.md, INC-017 and the
  hidden test's rule;
- refs and result summaries in the tools' own formats (app/tools/, graph._summarize);
- the confidence, computed by app/agent/confidence.py from the facts below;
- the total tokens and cost of a real run of this scenario (eval/results/20261025-041525.json).

It is not a real run: no model or sandbox ran. Its test runs have no sandbox_image_id, and it
has no patch row, so it can never be approved into a pull request.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.confidence import components, score
from app.core.config import Settings
from app.models import (
    EvidenceKind,
    EvidenceSourceType,
    Investigation,
    InvestigationEvent,
    InvestigationStatus,
    ModelTier,
    PatchStatus,
    Project,
    Stance,
    TestPhase,
    Verdict,
)
from app.schemas.events import (
    ConfidenceScored,
    Done,
    Event,
    EventPayload,
    EvidenceAdded,
    HypothesisProposed,
    PatchFile,
    PatchProposed,
    PatchStatusChanged,
    PrDrafted,
    ReportReady,
    StatusChanged,
    Step,
    StepCompleted,
    StepStarted,
    TestCompleted,
    TestOutput,
    ToolCall,
    ToolResult,
    VerdictReached,
    make_event,
)

_NAMESPACE = uuid.UUID("5c3e7a2e-0b1d-4f6a-9a51-7d2f3c1e8b40")


def uid(name: str) -> uuid.UUID:
    """The same id on every build, so seeding again replaces the replay, and fixtures diff."""
    return uuid.uuid5(_NAMESPACE, name)


INVESTIGATION_ID = uid("investigation")
PATCH_ID = uid("patch")
QUESTION = "Payment API returning 500s after today's deploy"
DEPLOYED_REF = "v1.4.8"
STARTED_AT = datetime(2026, 10, 6, 15, 25, tzinfo=UTC)  # just after the incident
TOTAL_TOKENS, TOTAL_COST_USD = 113_797, 0.053399  # the real run's

BAD_COMMIT = "5b0bb55c90"
DOCS_SHA = "f89f6df"  # v1.4.8, the indexed version
CONFIG = "src/paystream/database/config.py"
NEW_TEST = "tests/test_pool_fits_database.py"
_DEFAULTS = Settings.model_fields
MODELS = {
    ModelTier.NANO: _DEFAULTS["nemotron_nano"].default,
    ModelTier.SUPER: _DEFAULTS["nemotron_super"].default,
    ModelTier.ULTRA: _DEFAULTS["nemotron_ultra"].default,
}

DIFF = f"""--- a/{CONFIG}
+++ b/{CONFIG}
@@ -9,4 +9,4 @@
 MAX_CONNECTIONS = int(os.getenv("PAYSTREAM_DB_MAX_CONNECTIONS", "100"))
 INSTANCES = int(os.getenv("PAYSTREAM_INSTANCES", "5"))
-DB_POOL_SIZE = int(os.getenv("PAYSTREAM_DB_POOL_SIZE", "100"))
+DB_POOL_SIZE = int(os.getenv("PAYSTREAM_DB_POOL_SIZE", "20"))
 DB_POOL_TIMEOUT_SECONDS = 5
--- /dev/null
+++ b/{NEW_TEST}
@@ -0,0 +1,6 @@
+from paystream.database import config
+
+
+def test_pool_times_instances_fits_max_connections():
+    \"\"\"docs/database.md: DB_POOL_SIZE x INSTANCES must stay within max_connections.\"\"\"
+    assert config.DB_POOL_SIZE * config.INSTANCES <= config.MAX_CONNECTIONS
"""

PROGRESS = "{:<74}[100%]"  # pytest -q's progress line: one character per test
BASELINE_OUTPUT = (
    PROGRESS.format("F")
    + f"""
=================================== FAILURES ===================================
________________ test_pool_times_instances_fits_max_connections ________________

    def test_pool_times_instances_fits_max_connections():
        \"\"\"docs/database.md: DB_POOL_SIZE x INSTANCES must stay within max_connections.\"\"\"
>       assert config.DB_POOL_SIZE * config.INSTANCES <= config.MAX_CONNECTIONS
E       assert (100 * 5) <= 100
E        +  where 100 = <module 'paystream.database.config'>.DB_POOL_SIZE
E        +  and   5 = <module 'paystream.database.config'>.INSTANCES
E        +  and   100 = <module 'paystream.database.config'>.MAX_CONNECTIONS

{NEW_TEST}:6: AssertionError
=========================== short test summary info ============================
FAILED {NEW_TEST}::test_pool_times_instances_fits_max_connections
1 failed in 0.05s"""
)

PATCHED_OUTPUT = PROGRESS.format("." * 15) + "\n15 passed in 0.62s"

ROOT_CAUSE = (
    f"Commit {BAD_COMMIT} in v1.4.8 raised the default DB_POOL_SIZE from 20 to 100. With 5 API "
    "instances, PayStream can open 500 PostgreSQL connections against max_connections of 100, "
    "so new connections are refused and POST /payments fails with 500 after the 5 s pool timeout."
)

SUMMARY = f"""**Root cause:** commit {BAD_COMMIT} in v1.4.8 raised the default `DB_POOL_SIZE` \
from 20 to 100 [F3][F4]. Each of the 5 instances keeps its own pool, so PayStream can now open \
500 connections, five times Postgres' `max_connections` of 100 [F5][I1].

The errors began during the v1.4.8 rollout and hit every instance [F2][F7][I2]: 94 \
`POST /payments` requests failed with `OperationalError: too many connections` after waiting \
5 s for a pool slot [F1]. INC-017 had the same cause at a smaller scale, a pool of 60 [F6][I3].

The first error came 6 minutes into the rollout [F8]. That fits pools that open connections on \
demand, as the new instances took traffic; request volume was normal, so a traffic spike \
doesn't explain it.

**Fix, verified in the sandbox:** restore the default pool size to 20, and add a test for the \
documented rule `DB_POOL_SIZE × INSTANCES ≤ max_connections`. The new test fails on v1.4.8, and \
the whole suite passes with the fix."""

PR_BODY = f"""## What happened
Since v1.4.8 (6 Oct, 14:20), `POST /payments` fails with 500: `OperationalError: too many \
connections` on all 5 instances, 94 failed payments in the first hour.

## Why
Commit {BAD_COMMIT} raised the default `DB_POOL_SIZE` from 20 to 100. Each instance keeps its \
own pool, so 5 × 100 = 500 connections against Postgres `max_connections` of 100. \
`docs/database.md` requires `DB_POOL_SIZE × INSTANCES ≤ max_connections`, and INC-017 was the \
same failure with a pool of 60.

## Change
- `{CONFIG}`: default `DB_POOL_SIZE` back to 20 (5 × 20 = 100).
- `{NEW_TEST}`: a test for the documented rule, so a pool change that breaks it fails CI.

## Verified
- The new test fails on v1.4.8 (baseline), as expected.
- The whole suite passes with the fix (15 passed).

Investigated by RootTrace AI."""


class _Run:
    """Collects (seconds since the start, payload) pairs in publishing order."""

    def __init__(self) -> None:
        self.t = 0.0
        self.items: list[tuple[float, EventPayload]] = []
        self.facts: dict[str, dict] = {}  # label -> what the confidence score reads
        self._step: tuple[Step, int, float] | None = None

    def emit(self, after: float, payload: EventPayload) -> None:
        self.t = round(self.t + after, 3)
        self.items.append((self.t, payload))

    def begin(self, step: Step, tier: ModelTier | None = None, round: int = 1) -> None:
        model = MODELS[tier] if tier else None
        self.emit(0.05, StepStarted(step=step, round=round, tier=tier, model=model))
        self._step = (step, round, self.t)

    def end(self, after: float, summary: str) -> None:
        step, round, began = self._step
        self.emit(after, StepCompleted(step=step, round=round, duration_ms=0, summary=summary))
        self.items[-1][1].duration_ms = int((self.t - began) * 1000)

    def tool(
        self, step: Step, n: int, name: str, arguments: dict, seconds: float, summary: str, preview
    ) -> None:
        call_id = f"call_{step.value}_{n}"
        self.emit(0.4, ToolCall(call_id=call_id, step=step, tool=name, arguments=arguments))
        self.emit(
            seconds,
            ToolResult(
                call_id=call_id,
                tool=name,
                ok=True,
                duration_ms=int(seconds * 1000),
                summary=summary,
                output_preview=preview,
            ),
        )

    def fact(self, label, statement, tool, source_type, source_ref, excerpt) -> None:
        # Facts arrive neutral, as in a real run: the verifier's stance is kept on the evidence
        # rows and in the scores, not sent as an event.
        self.facts[label] = {"source_type": source_type, "stance": Stance.NEUTRAL, "weight": 1.0}
        self.emit(
            0.15,
            EvidenceAdded(
                evidence_id=uid(label),
                label=label,
                kind=EvidenceKind.FACT,
                statement=statement,
                tool=tool,
                source_type=source_type,
                source_ref=source_ref,
                excerpt=excerpt,
            ),
        )

    def inference(self, label: str, statement: str, cites: list[str]) -> None:
        self.emit(
            0.15,
            EvidenceAdded(
                evidence_id=uid(label),
                label=label,
                kind=EvidenceKind.INFERENCE,
                statement=statement,
                cites=[uid(c) for c in cites],
            ),
        )


def _investigate(run: _Run) -> None:
    s = Step.INVESTIGATE
    run.begin(s, ModelTier.SUPER)
    run.tool(
        s,
        1,
        "search_logs",
        {"since": "2026-10-06T13:00:00Z", "level": "ERROR"},
        0.38,
        "94 log lines; 94 OperationalError",
        '{"total": 94, "by_level": {"ERROR": 94}, "by_error_type": [{"error_type": '
        '"OperationalError", "count": 94, "first_seen": "2026-10-06T14:26:27Z", "last_seen": '
        '"2026-10-06T15:19:51Z"}], "by_route": [{"route": "/payments", "status": 500, "count": '
        '94}], "deploys": [{"ref": "log deploy at 2026-10-06T14:20:00+00:00", "message": "deploy '
        'started: release v1.4.8 (rolling, 5 instances)"}, {"ref": "log deploy at '
        '2026-10-06T14:30:00+00:00", "message": "deploy finished: release v1.4.8"}], "samples": '
        '[{"ref": "log trace_id=5a07979ff653e026", "instance": "api-3", "route": "/payments", '
        '"status": 500, "latency_ms": 5003, "message": "OperationalError: too many connections '
        "(FATAL: sorry, too many clients already); pool wait exceeded "
        'DB_POOL_TIMEOUT_SECONDS=5"}]}',
    )
    run.tool(
        s,
        2,
        "search_github_commits",
        {"query": "pool", "since": "2026-10-01"},
        0.21,
        f"1 commits; top: commit {BAD_COMMIT}",
        f'{{"commits": [{{"ref": "commit {BAD_COMMIT}", "sha": "{BAD_COMMIT}", "message": '
        f'"perf: raise DB pool size for peak traffic", "files": ["{CONFIG} (+1 -1)"]}}]}}',
    )
    run.tool(
        s,
        3,
        "get_commit_diff",
        {"sha": BAD_COMMIT},
        0.12,
        f"commit {BAD_COMMIT}",
        f'{{"ref": "commit {BAD_COMMIT}", "diff": "perf: raise DB pool size for peak traffic\\n\\n'
        f"--- a/{CONFIG}\\n+++ b/{CONFIG}\\n"
        '-DB_POOL_SIZE = int(os.getenv(\\"PAYSTREAM_DB_POOL_SIZE\\", \\"20\\"))\\n'
        '+DB_POOL_SIZE = int(os.getenv(\\"PAYSTREAM_DB_POOL_SIZE\\", \\"100\\"))"}',
    )
    run.tool(
        s,
        4,
        "get_file",
        {"path": CONFIG, "start_line": 1, "end_line": 12},
        0.09,
        f"{CONFIG} L1-12 @ {DEPLOYED_REF}",
        f'{{"ref": "{CONFIG} L1-12 @ {DEPLOYED_REF}", "content": "...\\n'
        ' 9  MAX_CONNECTIONS = int(os.getenv(\\"PAYSTREAM_DB_MAX_CONNECTIONS\\", \\"100\\"))\\n'
        '10  INSTANCES = int(os.getenv(\\"PAYSTREAM_INSTANCES\\", \\"5\\"))\\n'
        '11  DB_POOL_SIZE = int(os.getenv(\\"PAYSTREAM_DB_POOL_SIZE\\", \\"100\\"))"}',
    )
    doc_ref = f"docs/database.md § Database > Connection pooling L5-17 @ {DOCS_SHA}"
    run.tool(
        s,
        5,
        "search_documents",
        {"query": "connection pool size max_connections instances"},
        1.84,
        f"3 results; top: {doc_ref}",
        f'{{"results": [{{"ref": "{doc_ref}", "content": "This total **must stay at or below '
        "Postgres `max_connections`** (currently 100). With 5 instances, the **recommended pool "
        'size is 20 per instance or less**."}]}',
    )
    run.tool(
        s,
        6,
        "search_previous_incidents",
        {"query": "too many connections after deploy, payments 500"},
        1.62,
        "2 results; top: INC-017",
        '{"results": [{"ref": "INC-017", "content": "INC-017 · SEV1 · 2026-06-16\\nPayment API '
        '500s after pool size increase"}]}',
    )
    run.tool(
        s,
        7,
        "get_incident",
        {"key": "INC-017"},
        0.05,
        "INC-017",
        '{"ref": "INC-017", "title": "Payment API 500s after pool size increase", "root_cause": '
        '"A performance change raised DB_POOL_SIZE from 20 to 60. With 5 instances that is 300 '
        'connections against Postgres max_connections of 100."}',
    )
    sql = (
        "SELECT instance, count(*) AS failed FROM payments WHERE http_status = 500 "
        "AND created_at >= '2026-10-06T14:20:00Z' GROUP BY instance ORDER BY instance"
    )
    run.tool(
        s,
        8,
        "query_database",
        {"sql": sql},
        0.07,
        "5 rows",
        f'{{"ref": "sql: {sql}", "columns": ["instance", "failed"], "rows": [["api-1", 20], '
        '["api-2", 19], ["api-3", 23], ["api-4", 19], ["api-5", 13]], "truncated": false}',
    )
    run.fact(
        "F1",
        "94 POST /payments requests failed with 500 OperationalError: too many connections between "
        "14:26 and 15:19 on 6 Oct, each after waiting about 5 s.",
        "search_logs",
        EvidenceSourceType.LOG,
        "log trace_id=5a07979ff653e026",
        "OperationalError: too many connections (FATAL: sorry, too many clients already); pool "
        "wait exceeded DB_POOL_TIMEOUT_SECONDS=5",
    )
    run.fact(
        "F2",
        "Release v1.4.8 started rolling out to the 5 instances at 14:20 and finished at 14:30.",
        "search_logs",
        EvidenceSourceType.LOG,
        "log deploy at 2026-10-06T14:20:00+00:00",
        "deploy started: release v1.4.8 (rolling, 5 instances)",
    )
    run.fact(
        "F3",
        f"Commit {BAD_COMMIT}, 'perf: raise DB pool size for peak traffic', changed the default "
        "DB_POOL_SIZE from 20 to 100.",
        "get_commit_diff",
        EvidenceSourceType.COMMIT,
        f"commit {BAD_COMMIT}",
        '-DB_POOL_SIZE = int(os.getenv("PAYSTREAM_DB_POOL_SIZE", "20"))\n'
        '+DB_POOL_SIZE = int(os.getenv("PAYSTREAM_DB_POOL_SIZE", "100"))',
    )
    run.fact(
        "F4",
        "At v1.4.8, each of the 5 instances may open up to 100 connections, and MAX_CONNECTIONS "
        "is 100.",
        "get_file",
        EvidenceSourceType.CODE,
        f"{CONFIG} L1-12 @ {DEPLOYED_REF}",
        'MAX_CONNECTIONS = int(os.getenv("PAYSTREAM_DB_MAX_CONNECTIONS", "100"))\n'
        'INSTANCES = int(os.getenv("PAYSTREAM_INSTANCES", "5"))\n'
        'DB_POOL_SIZE = int(os.getenv("PAYSTREAM_DB_POOL_SIZE", "100"))',
    )
    run.fact(
        "F5",
        "docs/database.md requires DB_POOL_SIZE × INSTANCES to stay at or below max_connections "
        "(100), and recommends a pool of 20 with 5 instances.",
        "search_documents",
        EvidenceSourceType.DOC,
        doc_ref,
        "This total **must stay at or below Postgres `max_connections`** (currently 100). With 5 "
        "instances, the **recommended pool size is 20 per instance or less**.",
    )
    run.fact(
        "F6",
        "INC-017 (June) had the same symptoms when DB_POOL_SIZE was raised from 20 to 60, and "
        "was fixed by restoring 20.",
        "get_incident",
        EvidenceSourceType.INCIDENT,
        "INC-017",
        "A performance change raised DB_POOL_SIZE from 20 to 60. With 5 instances that is 300 "
        "connections against Postgres max_connections of 100.",
    )
    run.fact(
        "F7",
        "Failed payments since the deploy are spread over all 5 instances, 13 to 23 each.",
        "query_database",
        EvidenceSourceType.DATABASE,
        f"sql: {sql}",
        "api-1 20 · api-2 19 · api-3 23 · api-4 19 · api-5 13",
    )
    run.end(41.2, "8 tool calls, 7 facts")


def _build() -> _Run:
    run = _Run()
    run.emit(0, StatusChanged(status=InvestigationStatus.RUNNING))

    run.begin(Step.TRIAGE, ModelTier.NANO)
    run.end(
        2.3,
        "POST /payments has returned 500s since the v1.4.8 deploy on 6 Oct: look at that "
        "release's changes, the payment logs and the database.",
    )
    run.begin(Step.RECALL_MEMORY)
    run.end(0.3, "0 memories recalled (used as hints, not evidence)")
    run.begin(Step.PLAN, ModelTier.SUPER)
    run.end(7.4, "5 steps planned")

    _investigate(run)

    run.begin(Step.COMPARE_EVIDENCE, ModelTier.SUPER)
    run.inference(
        "I1",
        "With DB_POOL_SIZE at 100 on 5 instances, PayStream can open 500 connections: five times "
        "Postgres' max_connections of 100.",
        ["F3", "F4", "F5"],
    )
    run.inference(
        "I2",
        "The errors start during the v1.4.8 rollout and hit every instance, which points to a "
        "change shipped in that release rather than one failing host.",
        ["F1", "F2", "F7"],
    )
    run.inference(
        "I3",
        "INC-017 had the same cause and symptoms at a smaller scale (a pool of 60: 300 "
        "connections).",
        ["F5", "F6"],
    )
    run.end(11.8, "3 inferences")

    top = uid("H1")
    run.begin(Step.HYPOTHESIZE, ModelTier.ULTRA)
    run.emit(
        22.4,
        HypothesisProposed(
            hypothesis_id=top,
            statement=f"Commit {BAD_COMMIT} in v1.4.8 raised DB_POOL_SIZE from 20 to 100; with 5 "
            "instances that allows 500 connections against max_connections of 100, so Postgres "
            "refuses connections and POST /payments fails with 500 after the pool timeout.",
            rank=1,
        ),
    )
    run.emit(
        0.1,
        HypothesisProposed(
            hypothesis_id=uid("H2"),
            statement="A traffic spike after the deploy exhausted the database's connections.",
            rank=2,
        ),
    )
    run.end(0.3, f"Commit {BAD_COMMIT} in v1.4.8 raised DB_POOL_SIZE from 20 to 100")

    run.begin(Step.VERIFY, ModelTier.ULTRA)
    run.tool(
        Step.VERIFY,
        1,
        "search_logs",
        {
            "route": "/payments",
            "since": "2026-10-06T14:20:00Z",
            "until": "2026-10-06T14:26:27Z",
        },
        0.31,
        "49 log lines",
        '{"total": 49, "by_level": {"INFO": 49}, "by_error_type": [{"error_type": "Deploy", '
        '"count": 1}], "by_route": [{"route": "/payments", "status": 200, "count": 10}], '
        '"samples": [{"ref": "log trace_id=d8f16adf91b7584a", "message": "deploy started: '
        'release v1.4.8 (rolling, 5 instances)"}]}',
    )
    run.fact(
        "F8",
        "For the first 6 minutes of the rollout (14:20 to 14:26), POST /payments still succeeded: "
        "the 10 payments in that window all returned 200.",
        "search_logs",
        EvidenceSourceType.LOG,
        "log trace_id=d8f16adf91b7584a",
        "deploy started: release v1.4.8 (rolling, 5 instances)",
    )
    for label in ("F1", "F2", "F3", "F4", "F5", "F6", "F7"):
        run.facts[label]["stance"] = Stance.SUPPORTS
    run.facts["F8"]["stance"] = Stance.CONTRADICTS
    contradictions = [uid("F8")]
    run.emit(
        19.3,
        VerdictReached(
            hypothesis_id=top,
            verdict=Verdict.SUPPORTED,
            notes="Every fact fits: the commit, the code at v1.4.8, the documented limit, all 5 "
            "instances failing and INC-017. The 6-minute delay [F8] fits pools opening "
            "connections on demand as the new instances took traffic; the volume was normal, "
            "so a traffic spike (H2) doesn't explain it.",
        ),
    )
    run.end(0.2, "supported: Every fact fits: the commit, the code at v1.4.8, the documented limit")

    facts = list(run.facts.values())
    before_fix = components(facts, Verdict.SUPPORTED)
    run.begin(Step.SCORE_CONFIDENCE)
    run.emit(
        0.02,
        ConfidenceScored(
            score=score(before_fix), components=before_fix, contradiction_ids=contradictions
        ),
    )
    run.end(0.01, f"{score(before_fix):.0%}")

    run.begin(Step.GENERATE_PATCH, ModelTier.SUPER)
    run.emit(
        18.6,
        PatchProposed(
            patch_id=PATCH_ID,
            attempt=1,
            diff=DIFF,
            files=[
                PatchFile(path=CONFIG, explanation="Default pool back to 20: 5 × 20 = 100."),
                PatchFile(path=NEW_TEST, explanation="Fails whenever pool × instances > limit."),
            ],
            explanation="Restore the documented pool size, and test the rule it must follow.",
        ),
    )
    run.end(0.1, f"1 file(s) changed, test {NEW_TEST}")

    run.begin(Step.RUN_TESTS)
    for phase, command, output, exit_code, passed, failed, seconds in (
        (TestPhase.BASELINE, f"pytest -q {NEW_TEST}", BASELINE_OUTPUT, 1, 0, 1, 6.4),
        (TestPhase.PATCHED, "pytest -q", PATCHED_OUTPUT, 0, 15, 0, 7.9),
    ):
        lines = output.splitlines()
        run.emit(seconds - 0.1, TestOutput(patch_id=PATCH_ID, phase=phase, line=lines[0]))
        for line in lines[1:]:
            run.emit(0, TestOutput(patch_id=PATCH_ID, phase=phase, line=line))
        run.emit(
            0.1,
            TestCompleted(
                test_run_id=uid(f"test run {phase.value}"),
                patch_id=PATCH_ID,
                phase=phase,
                command=command,
                exit_code=exit_code,
                passed=passed,
                failed=failed,
                duration_ms=int(seconds * 1000),
                sandbox_image_id=None,  # no sandbox ran: this is a replay
                as_expected=True,
            ),
        )
    run.end(0.1, "baseline failed as expected; patched passed")

    run.begin(Step.VERIFY_FIX, ModelTier.ULTRA)
    notes = (
        "The new test reproduces the documented rule, fails on v1.4.8 and passes with the fix; "
        "the rest of the suite still passes."
    )
    run.emit(9.8, PatchStatusChanged(patch_id=PATCH_ID, status=PatchStatus.VERIFIED, notes=notes))
    after_fix = components(facts, Verdict.SUPPORTED, fix_verified=True)
    final = score(after_fix)
    run.emit(
        0.02,
        ConfidenceScored(score=final, components=after_fix, contradiction_ids=contradictions),
    )
    run.end(0.1, f"verified: {notes}")

    run.begin(Step.WRITE_REPORT, ModelTier.ULTRA)
    run.emit(
        23.7,
        ReportReady(
            root_cause=ROOT_CAUSE,
            summary=SUMMARY,
            verdict=Verdict.SUPPORTED,
            confidence=final,
            fact_ids=[uid(f"F{n}") for n in range(1, 9)],
            inference_ids=[uid(f"I{n}") for n in range(1, 4)],
            contradiction_ids=contradictions,
            fix="verified",
            patch_id=PATCH_ID,
            next_steps=[
                "Approve the draft PR and release it as v1.4.9.",
                "Add a startup check that refuses DB_POOL_SIZE × INSTANCES above MAX_CONNECTIONS.",
                "For more capacity, add read replicas or raise max_connections first "
                "(docs/database.md).",
            ],
        ),
    )
    run.end(0.1, ROOT_CAUSE[:200])

    title = "Restore DB_POOL_SIZE to 20 so 5 instances fit max_connections"
    run.begin(Step.DRAFT_PR, ModelTier.SUPER)
    run.emit(6.2, PrDrafted(patch_id=PATCH_ID, title=title, body=PR_BODY))
    run.end(0.1, title)
    run.begin(Step.SAVE_MEMORIES, ModelTier.NANO)
    run.end(2.1, "no memories saved")

    run.emit(0.05, StatusChanged(status=InvestigationStatus.AWAITING_APPROVAL))
    run.emit(
        0.01,
        Done(
            status=InvestigationStatus.COMPLETED,  # the run is done; the PR waits for approval
            duration_ms=int(run.t * 1000),
            total_tokens=TOTAL_TOKENS,
            total_cost_usd=TOTAL_COST_USD,
        ),
    )
    return run


def events() -> list[Event]:
    """The replay's events, numbered from 1, as /events returns them."""
    return [
        make_event(seq, payload, STARTED_AT + timedelta(seconds=at))
        for seq, (at, payload) in enumerate(_build().items, start=1)
    ]


async def seed(session: AsyncSession, project: Project) -> Investigation:
    """Insert the replay into `project`, replacing an earlier copy. Commits."""
    replay = events()
    report = next(e.payload for e in replay if e.type == "report")
    confidence = [e.payload for e in replay if e.type == "confidence"][-1]
    await session.execute(delete(Investigation).where(Investigation.id == INVESTIGATION_ID))
    investigation = Investigation(
        id=INVESTIGATION_ID,
        project_id=project.id,
        user_id=project.owner_id,
        question=QUESTION,
        status=InvestigationStatus.AWAITING_APPROVAL,
        root_cause=report.root_cause,
        confidence=confidence.score,
        confidence_breakdown={part.name: part.value for part in confidence.components},
        report={**report.model_dump(mode="json"), "deployed_ref": DEPLOYED_REF},
        started_at=STARTED_AT,
        finished_at=replay[-1].ts,
        total_tokens=TOTAL_TOKENS,
        total_cost_usd=Decimal(str(TOTAL_COST_USD)),
    )
    session.add(investigation)
    await session.flush()
    session.add_all(
        InvestigationEvent(
            investigation_id=INVESTIGATION_ID,
            seq=event.seq,
            type=event.type,
            payload=event.payload.model_dump(mode="json"),
            ts=event.ts,
        )
        for event in replay
    )
    await session.commit()
    return investigation
