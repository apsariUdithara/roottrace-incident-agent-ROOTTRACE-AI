"""The investigation agent: a LangGraph state graph (docs/diagrams/02-agent-workflow.svg).

    triage -> recall_memory -> plan -> investigate -> compare_evidence -> hypothesize -> verify
                                          ^                                               |
                                          +---- weak verdict and a round left ------------+
                                                                                          v
                                                         score_confidence -> write_report -> END

The graph decides the ORDER; the model decides the CONTENT of each step. Every step speaks to
the outside world only through the event bus, and every claim is evidence with a citation.

Two kinds of evidence (the product's core rule):
- fact: something a tool showed. Must carry its source and a verbatim excerpt, and the excerpt
  is checked against the tool's actual output; the model cannot invent one.
- inference: a conclusion drawn from facts. Must cite the facts it uses.
"""

import json
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agent import fix, memory, prompts
from app.agent.confidence import components, score
from app.agent.events import EventBus
from app.llm.router import LLMRouter
from app.models import (
    Evidence,
    EvidenceKind,
    EvidenceSourceType,
    Hypothesis,
    Investigation,
    InvestigationStatus,
    ModelTier,
    Patch,
    PatchStatus,
    SpanKind,
    SpanStatus,
    Stance,
    TestPhase,
    TestRun,
    TraceSpan,
    Verdict,
)
from app.sandbox import NebiusSandbox, SandboxError
from app.schemas.events import (
    ConfidenceScored,
    Done,
    ErrorRaised,
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
)
from app.tools import TOOLS, ToolContext, ToolError, call_tool, openai_schemas
from app.tools.database import describe_tables

MAX_ROUNDS = 2  # investigate at most twice
INVESTIGATE_BUDGET = 12  # tool calls per investigate round
VERIFY_BUDGET = 4  # tool calls the verifier may use to look for contradictions
EXTRACT_BATCH = 4  # tool results per extraction call
FACTS_PER_BATCH = 5
EXTRACT_MAX_TOKENS = 6000  # room for reasoning plus ~5 facts, not for a runaway answer
PREVIEW_CHARS = 1500
MAX_PATCH_ATTEMPTS = 2
TEST_OUTPUT_LINES = 60  # streamed per test run
FEEDBACK_CHARS = 3000
EXTRACT_CHARS = 3500  # per tool result shown to fact extraction

# Which kind of source each tool observes. Deterministic, so the model can't mislabel a fact.
TOOL_SOURCE = {
    "search_logs": EvidenceSourceType.LOG,
    "query_database": EvidenceSourceType.DATABASE,
    "search_github_commits": EvidenceSourceType.COMMIT,
    "get_commit_diff": EvidenceSourceType.COMMIT,
    "get_file": EvidenceSourceType.CODE,
    "search_code": EvidenceSourceType.CODE,
    "search_documents": EvidenceSourceType.DOC,
    "search_previous_incidents": EvidenceSourceType.INCIDENT,
    "get_incident": EvidenceSourceType.INCIDENT,
    "search_github_issues": EvidenceSourceType.ISSUE,
}


# --- What each step must return (validated by the router) -----------------------------------


class Triage(BaseModel):
    summary: str
    affected: str
    symptoms: list[str]
    suspected_start: str | None = None
    search_terms: list[str]


class Plan(BaseModel):
    steps: list[str] = Field(min_length=1, max_length=8)


class FactItem(BaseModel):
    statement: str
    call: int
    excerpt: str
    source_ref: str


class FactList(BaseModel):
    facts: list[FactItem]


class InferenceItem(BaseModel):
    statement: str
    cites: list[str]


class Comparison(BaseModel):
    inferences: list[InferenceItem]
    conflicts: list[str] = []


class HypothesisItem(BaseModel):
    statement: str
    supporting: list[str]


class Hypotheses(BaseModel):
    fault_found: bool = True  # False: the evidence shows nothing is broken
    hypotheses: list[HypothesisItem] = Field(min_length=1, max_length=3)


class VerdictOut(BaseModel):
    verdict: Literal["supported", "weak", "refuted"]
    supports: list[str]
    contradicts: list[str]
    gaps: list[str] = []
    notes: str


class ReportOut(BaseModel):
    root_cause: str | None
    summary: str
    next_steps: list[str]


class AgentState(TypedDict, total=False):
    round: int
    triage: dict
    plan: list[str]
    facts: list[
        dict
    ]  # {id, label, statement, tool, source_type, source_ref, excerpt, stance, weight}
    inferences: list[dict]  # {id, label, statement, cites: [labels]}
    hypotheses: list[dict]  # {id, statement}, best first
    verdict: dict  # {hypothesis_id, verdict, notes, gaps}
    confidence: float
    report: dict
    attempt: int  # patch attempt, 1 or 2
    patch: dict
    tests: dict | None  # baseline and patched results; None if the sandbox was unavailable
    fix: str  # final PatchStatus value
    blocked: bool  # the latest patch was rejected before testing (it edited existing tests)
    fix_feedback: str | None
    hints: str  # recalled memories, as text for the prompts
    no_fault: bool  # the hypothesis step found nothing broken


# --- Helpers ----------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _strings(value) -> list[str]:
    """Every string (and number) inside a decoded tool result."""
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [str(value)] if value is not None else []


def _refs(value) -> list[tuple[str, str]]:
    """(ref, text of that item) for every result item that carries a citation."""
    found = []
    if isinstance(value, dict):
        if "ref" in value:
            found.append((str(value["ref"]), _norm(" ".join(_strings(value)))))
        for v in value.values():
            if isinstance(v, (dict, list)):
                found += _refs(v)
    elif isinstance(value, list):
        for v in value:
            found += _refs(v)
    return found


def _summarize(data: dict) -> str:
    """One line for the activity feed."""
    if "error" in data:
        return f"error: {data['error'][:120]}"
    if "total" in data:
        errors = ", ".join(
            f"{e['count']} {e['error_type']}" for e in data.get("by_error_type", [])[:2]
        )
        return f"{data['total']} log lines" + (f"; {errors}" if errors else "")
    for key, noun in (("results", "results"), ("commits", "commits"), ("rows", "rows")):
        if key in data:
            items = data[key]
            first = (
                items[0].get("ref") or items[0].get("message")
                if items and isinstance(items[0], dict)
                else ""
            )
            return f"{len(items)} {noun}" + (f"; top: {first}" if first else "")
    return data.get("ref", "ok")


def _fact_lines(facts: list[dict]) -> str:
    return (
        "\n".join(f"{f['label']}: {f['statement']} (source: {f['source_ref']})" for f in facts)
        or "(none)"
    )


def _inference_lines(inferences: list[dict]) -> str:
    return (
        "\n".join(f"{i['label']}: {i['statement']} [{', '.join(i['cites'])}]" for i in inferences)
        or "(none)"
    )


FIX_SUMMARY = {
    "none": "no fix was attempted (the root cause was not verified)",
    "verified": "a fix was written; its regression test failed before and the whole suite "
    "passed after, in a Nebius sandbox, and it was judged to address the root cause",
    "untested": "a fix was written but could not be tested in the sandbox",
    "tests_failed": "a fix was attempted but did not pass the tests: no safe fix found",
    "rejected": "a fix was attempted but did not address the root cause: no safe fix found",
}
REPORT_FIX = {
    "none": "none",
    "verified": "verified",
    "untested": "untested",
    "tests_failed": "failed",
    "rejected": "failed",
}


def _counts(result: dict) -> str:
    return f"{result['passed'] or 0} passed, {result['failed'] or 0} failed"


# The investigation checklist, as the tools that satisfy each item.
CHECKLIST = {
    "what changed (search_github_commits, get_commit_diff)": {
        "search_github_commits",
        "get_commit_diff",
    },
    "the deployed code (get_file)": {"get_file", "search_code"},
    "what was observed (search_logs)": {"search_logs", "query_database"},
    "the documented rules (search_documents)": {"search_documents"},
    "past incidents (search_previous_incidents)": {"search_previous_incidents", "get_incident"},
}


def _call_key(call: dict) -> str:
    """A tool call in a canonical form, so the same call with reordered arguments matches."""
    try:
        args = json.loads(call["arguments"] or "{}")
    except json.JSONDecodeError:
        args = call["arguments"]
    return call["name"].removeprefix("functions.") + json.dumps(args, sort_keys=True)


def _progress_note(step: Step, transcript: list[dict], left: int) -> str:
    note = f"You have {left} tool call(s) left."
    if step == Step.INVESTIGATE:
        done = {t["tool"].removeprefix("functions.") for t in transcript}
        missing = [item for item, tools in CHECKLIST.items() if not done & tools]
        if missing:
            note += " Not checked yet: " + "; ".join(missing) + ". Cover these first."
    return note


# --- The agent --------------------------------------------------------------------------------


class Investigator:
    def __init__(
        self,
        investigation: Investigation,
        tools: ToolContext,
        bus: EventBus,
        reported_at: datetime | None = None,
        sandbox: NebiusSandbox | None = None,
        test_command: str = "pytest -q",
        setup_command: str | None = None,
    ):
        self.inv = investigation
        self.ctx = tools
        self.session = tools.session
        self.router: LLMRouter = tools.router
        self.bus = bus
        self.reported_at = (reported_at or datetime.now(UTC)).isoformat(timespec="minutes")
        self.sandbox = sandbox or NebiusSandbox()
        self.test_command, self.setup_command = test_command, setup_command
        self.base: Path | None = None  # snapshot of the deployed code
        self.tmp: Path | None = None
        self.image: str | None = None  # sandbox environment image
        self.data_tables: str | None = None  # query_database schema, for the prompts
        self.graph = self._build()

    # graph ------------------------------------------------------------------------------------

    def _build(self):
        g = StateGraph(AgentState)
        for name in (
            "triage",
            "recall_memory",
            "plan",
            "investigate",
            "compare_evidence",
            "hypothesize",
            "verify",
            "score_confidence",
            "generate_patch",
            "run_tests",
            "verify_fix",
            "draft_pr",
            "write_report",
            "save_memories",
        ):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "triage")
        g.add_edge("triage", "recall_memory")
        g.add_edge("recall_memory", "plan")
        g.add_edge("plan", "investigate")
        g.add_conditional_edges(
            "investigate", self.after_investigate, ["investigate", "compare_evidence"]
        )
        g.add_edge("compare_evidence", "hypothesize")
        g.add_edge("hypothesize", "verify")
        g.add_conditional_edges("verify", self.after_verify, ["investigate", "score_confidence"])
        g.add_conditional_edges(
            "score_confidence", self.after_score, ["generate_patch", "write_report"]
        )
        g.add_conditional_edges(
            "generate_patch", self.after_patch, ["run_tests", "generate_patch", "write_report"]
        )
        g.add_edge("run_tests", "verify_fix")
        g.add_conditional_edges("verify_fix", self.after_fix, ["generate_patch", "write_report"])
        g.add_conditional_edges("write_report", self.after_report, ["draft_pr", "save_memories"])
        g.add_edge("draft_pr", "save_memories")
        g.add_edge("save_memories", END)
        return g.compile()

    @staticmethod
    def after_investigate(state: AgentState) -> str:
        if not state["facts"] and state["round"] < MAX_ROUNDS:
            return "investigate"  # nothing established yet: don't hypothesise from nothing
        return "compare_evidence"

    @staticmethod
    def after_score(state: AgentState) -> str:
        # Only a verified root cause gets a fix: patching a guess could hide the real problem.
        # "No fault found" gets no fix either: there is nothing to repair.
        supported = state["verdict"]["verdict"] == Verdict.SUPPORTED
        return "generate_patch" if supported and not state.get("no_fault") else "write_report"

    @classmethod
    def after_patch(cls, state: AgentState) -> str:
        # A patch that edits existing tests is never run: treat it like a failed attempt.
        return cls.after_fix(state) if state.get("blocked") else "run_tests"

    @staticmethod
    def after_fix(state: AgentState) -> str:
        retry = state["fix"] in (PatchStatus.TESTS_FAILED, PatchStatus.REJECTED)
        if retry and state["attempt"] < MAX_PATCH_ATTEMPTS:
            return "generate_patch"
        return "write_report"

    @staticmethod
    def after_report(state: AgentState) -> str:
        return "draft_pr" if state.get("fix") == PatchStatus.VERIFIED else "save_memories"

    @staticmethod
    def after_verify(state: AgentState) -> str:
        if state["verdict"]["verdict"] == Verdict.WEAK and state["round"] < MAX_ROUNDS:
            return "investigate"
        return "score_confidence"

    # plumbing ---------------------------------------------------------------------------------

    @asynccontextmanager
    async def step(self, step: Step, tier: str | None = None, round: int = 1):
        model = self.router.model_for(tier) if tier else None
        await self.bus.publish(StepStarted(step=step, round=round, tier=tier, model=model))
        info = {"summary": None}
        start = time.perf_counter()
        yield info
        await self.bus.publish(
            StepCompleted(
                step=step,
                round=round,
                duration_ms=int((time.perf_counter() - start) * 1000),
                summary=info["summary"],
            )
        )

    async def ask(
        self, tier: str, prompt: str, schema: type[BaseModel], name: str, max_tokens=3000
    ):
        messages = [
            {"role": "system", "content": prompts.SYSTEM},
            {"role": "user", "content": prompt},
        ]
        result = await self.router.complete(
            tier,
            messages,
            schema=schema,
            max_tokens=max_tokens,
            name=f"{name}.{prompts.PROMPT_VERSION}",
        )
        return result.parsed

    async def run_tools(self, step: Step, prompt: str, budget: int) -> list[dict]:
        """Let the model call tools until it stops or the budget runs out (a ReAct loop).

        After each turn the model is told how many calls it has left and, while investigating,
        which kinds of evidence it has not checked yet, so the budget is not spent before the
        checklist is covered. An exact repeat of an earlier call is not run again.
        """
        if self.data_tables is None:  # looked up once per investigation
            self.data_tables = await describe_tables(self.ctx)
        if self.data_tables:
            prompt += (
                "\nProduction data for query_database (tables and columns): " + self.data_tables
            )
        messages = [
            {"role": "system", "content": prompts.SYSTEM},
            {"role": "user", "content": prompt},
        ]
        transcript: list[dict] = []
        earlier: dict[str, int] = {}  # normalised call -> its number, to spot exact repeats
        used = 0  # calls spent, including refused repeats
        schemas = openai_schemas()
        while used < budget:
            result = await self.router.complete(
                "super",
                messages,
                tools=schemas,
                max_tokens=1500,
                name=f"{step}.{prompts.PROMPT_VERSION}",
            )
            if not result.tool_calls:
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c["arguments"]},
                        }
                        for c in result.tool_calls
                    ],
                }
            )
            for call in result.tool_calls:
                key = _call_key(call)
                if used >= budget:
                    output = json.dumps({"error": "tool budget used up; summarise what you found"})
                elif key in earlier:
                    used += 1
                    output = json.dumps(
                        {
                            "error": f"You already made this exact call (call {earlier[key]}); "
                            "its result is above. Use different arguments or another tool."
                        }
                    )
                else:
                    used += 1
                    output = await self.call(step, call)
                    transcript.append(
                        {
                            "n": len(transcript) + 1,
                            "tool": call["name"],
                            "arguments": call["arguments"],
                            "output": output,
                        }
                    )
                    earlier[key] = len(transcript)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
            if used < budget:
                messages.append(
                    {"role": "user", "content": _progress_note(step, transcript, budget - used)}
                )
        return transcript

    async def call(self, step: Step, call: dict) -> str:
        call_id = call["id"] or uuid.uuid4().hex
        try:
            args = json.loads(call["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {"raw": call["arguments"]}
        await self.bus.publish(
            ToolCall(
                call_id=call_id,
                step=step,
                tool=call["name"],
                arguments=args if isinstance(args, dict) else {},
            )
        )
        output = await call_tool(self.ctx, call["name"], call["arguments"])
        trace = self.ctx.trace[-1]
        data = json.loads(output) if output.startswith("{") and output.endswith("}") else {}
        await self.bus.publish(
            ToolResult(
                call_id=call_id,
                tool=call["name"],
                ok=trace["ok"],
                duration_ms=trace["latency_ms"],
                summary=_summarize(data) if data else "result truncated",
                output_preview=output[:PREVIEW_CHARS],
                error=data.get("error") if data else None,
            )
        )
        return output

    async def record_facts(self, transcript: list[dict], state: AgentState) -> list[dict]:
        """Ask for the facts in the tool results, then keep only those we can verify."""
        if not transcript:
            return []
        # Small batches: a smaller answer is less likely to go wrong (we have seen a model emit
        # blank lines until the token limit), and a failed batch only loses its own facts.
        found = FactList(facts=[])
        for i in range(0, len(transcript), EXTRACT_BATCH):
            text = "\n\n".join(
                f"[call {t['n']}] {t['tool']}({t['arguments']})\n{t['output'][:EXTRACT_CHARS]}"
                for t in transcript[i : i + EXTRACT_BATCH]
            )
            try:
                part = await self.ask(
                    "super",
                    prompts.EXTRACT_FACTS.format(
                        question=self.inv.question,
                        transcript=text,
                        limit=FACTS_PER_BATCH,
                        known=_fact_lines(state.get("facts", [])),
                    ),
                    FactList,
                    "extract_facts",
                    max_tokens=EXTRACT_MAX_TOKENS,
                )
                found.facts += part.facts
            except Exception as exc:  # noqa: BLE001 - lose this batch's facts, not the run
                await self.bus.publish(
                    ErrorRaised(message=f"fact extraction failed: {exc}"[:500], fatal=False)
                )
        known = {(f["source_ref"], _norm(f["excerpt"])) for f in state.get("facts", [])}
        facts = []
        for item in found.facts:
            source = next((t for t in transcript if t["n"] == item.call), None)
            if source is None or source["tool"] not in TOOL_SOURCE or not item.excerpt.strip():
                continue
            try:
                data = json.loads(source["output"])
            except json.JSONDecodeError:
                data = {}
            excerpt = _norm(item.excerpt)
            # The grounding check: the excerpt must really be in what the tool returned.
            if excerpt not in _norm(source["output"]) and excerpt not in _norm(
                " ".join(_strings(data))
            ):
                continue
            refs = _refs(data)
            ref = (
                next((r for r, _ in refs if r == item.source_ref), None)
                or next((r for r, body in refs if excerpt in body), None)
                or (refs[0][0] if refs else f"{source['tool']}({source['arguments']})")
            )
            if (ref, excerpt) in known:
                continue
            known.add((ref, excerpt))
            label = f"F{len(state.get('facts', [])) + len(facts) + 1}"
            row = Evidence(
                investigation_id=self.inv.id,
                kind=EvidenceKind.FACT,
                statement=item.statement,
                tool=source["tool"],
                source_type=TOOL_SOURCE[source["tool"]],
                source_ref=ref[:1000],
                excerpt=item.excerpt.strip(),
            )
            self.session.add(row)
            await self.session.flush()
            fact = {
                "id": str(row.id),
                "label": label,
                "statement": item.statement,
                "tool": source["tool"],
                "source_type": row.source_type.value,
                "source_ref": row.source_ref,
                "excerpt": row.excerpt,
                "stance": Stance.NEUTRAL.value,
                "weight": 1.0,
            }
            facts.append(fact)
            await self.bus.publish(
                EvidenceAdded(
                    evidence_id=row.id,
                    label=label,
                    kind=EvidenceKind.FACT,
                    statement=item.statement,
                    tool=source["tool"],
                    source_type=row.source_type,
                    source_ref=row.source_ref,
                    excerpt=row.excerpt,
                )
            )
        return facts

    # nodes ------------------------------------------------------------------------------------

    async def triage(self, state: AgentState) -> AgentState:
        async with self.step(Step.TRIAGE, "nano") as info:
            t = await self.ask(
                "nano",
                prompts.TRIAGE.format(question=self.inv.question, reported_at=self.reported_at),
                Triage,
                "triage",
            )
            info["summary"] = t.summary[:200]
        return {"triage": t.model_dump()}

    async def recall_memory(self, state: AgentState) -> AgentState:
        async with self.step(Step.RECALL_MEMORY) as info:
            query = f"{self.inv.question} {state['triage'].get('summary', '')}"
            recalled = await memory.recall(self.session, self.router, self.inv.project_id, query)
            info["summary"] = f"{len(recalled)} memories recalled (used as hints, not evidence)"
        return {"hints": memory.as_hints(recalled)}

    async def plan(self, state: AgentState) -> AgentState:
        async with self.step(Step.PLAN, "super") as info:
            tools = "\n".join(f"- {t.name}: {t.description}" for t in TOOLS.values())
            p = await self.ask(
                "super",
                prompts.PLAN.format(
                    question=self.inv.question,
                    reported_at=self.reported_at,
                    ref=self.ctx.ref,
                    triage=json.dumps(state["triage"]),
                    tools=tools,
                    hints=state.get("hints", ""),
                ),
                Plan,
                "plan",
            )
            info["summary"] = f"{len(p.steps)} steps planned"
        return {"plan": p.steps}

    async def investigate(self, state: AgentState) -> AgentState:
        rnd = state.get("round", 0) + 1
        async with self.step(Step.INVESTIGATE, "super", rnd) as info:
            focus = ""
            if rnd > 1 and "verdict" in state:
                focus = prompts.ROUND_FOCUS.format(
                    round=rnd,
                    gaps="\n".join(f"- {g}" for g in state["verdict"]["gaps"]),
                    facts=_fact_lines(state["facts"]),
                )
            prompt = prompts.INVESTIGATE.format(
                question=self.inv.question,
                reported_at=self.reported_at,
                ref=self.ctx.ref,
                plan="\n".join(f"{i + 1}. {s}" for i, s in enumerate(state["plan"])),
                focus=focus,
                hints=state.get("hints", ""),
            )
            transcript = await self.run_tools(Step.INVESTIGATE, prompt, INVESTIGATE_BUDGET)
            new = await self.record_facts(transcript, state)
            info["summary"] = f"{len(transcript)} tool calls, {len(new)} facts"
        return {"round": rnd, "facts": state.get("facts", []) + new}

    async def compare_evidence(self, state: AgentState) -> AgentState:
        async with self.step(Step.COMPARE_EVIDENCE, "super", state["round"]) as info:
            c = await self.ask(
                "super",
                prompts.COMPARE.format(
                    question=self.inv.question, facts=_fact_lines(state["facts"])
                ),
                Comparison,
                "compare",
            )
            by_label = {f["label"]: f for f in state["facts"]}
            inferences = list(state.get("inferences", []))
            for item in c.inferences:
                cites = [label for label in item.cites if label in by_label]
                if not cites:
                    continue
                label = f"I{len(inferences) + 1}"
                row = Evidence(
                    investigation_id=self.inv.id,
                    kind=EvidenceKind.INFERENCE,
                    statement=item.statement,
                    cites=[uuid.UUID(by_label[x]["id"]) for x in cites],
                )
                self.session.add(row)
                await self.session.flush()
                inferences.append(
                    {"id": str(row.id), "label": label, "statement": item.statement, "cites": cites}
                )
                await self.bus.publish(
                    EvidenceAdded(
                        evidence_id=row.id,
                        label=label,
                        kind=EvidenceKind.INFERENCE,
                        statement=item.statement,
                        cites=row.cites,
                    )
                )
            info["summary"] = f"{len(inferences) - len(state.get('inferences', []))} inferences"
        return {"inferences": inferences}

    async def hypothesize(self, state: AgentState) -> AgentState:
        async with self.step(Step.HYPOTHESIZE, "ultra", state["round"]) as info:
            h = await self.ask(
                "ultra",
                prompts.HYPOTHESIZE.format(
                    question=self.inv.question,
                    facts=_fact_lines(state["facts"]),
                    inferences=_inference_lines(state.get("inferences", [])),
                ),
                Hypotheses,
                "hypothesize",
            )
            hypotheses = []
            for rank, item in enumerate(h.hypotheses, start=1):
                row = Hypothesis(investigation_id=self.inv.id, statement=item.statement, rank=rank)
                self.session.add(row)
                await self.session.flush()
                hypotheses.append({"id": str(row.id), "statement": item.statement})
                await self.bus.publish(
                    HypothesisProposed(
                        hypothesis_id=row.id,
                        statement=item.statement,
                        rank=rank,
                        round=state["round"],
                    )
                )
            info["summary"] = hypotheses[0]["statement"][:200]
        return {"hypotheses": hypotheses, "no_fault": not h.fault_found}

    async def verify(self, state: AgentState) -> AgentState:
        top = state["hypotheses"][0]
        async with self.step(Step.VERIFY, "ultra", state["round"]) as info:
            transcript = await self.run_tools(
                Step.VERIFY,
                prompts.VERIFY_PROBE.format(
                    question=self.inv.question,
                    hypothesis=top["statement"],
                    facts=_fact_lines(state["facts"]),
                    budget=VERIFY_BUDGET,
                ),
                VERIFY_BUDGET,
            )
            facts = state["facts"] + await self.record_facts(transcript, state)
            v = await self.ask(
                "ultra",
                prompts.VERDICT.format(
                    question=self.inv.question,
                    hypothesis=top["statement"],
                    facts=_fact_lines(facts),
                    inferences=_inference_lines(state.get("inferences", [])),
                ),
                VerdictOut,
                "verdict",
            )

            stance = {
                **{x: Stance.SUPPORTS for x in v.supports},
                **{x: Stance.CONTRADICTS for x in v.contradicts},
            }
            hypothesis_id = uuid.UUID(top["id"])
            for fact in facts:
                if fact["label"] in stance:
                    fact["stance"] = stance[fact["label"]].value
                    row = await self.session.get(Evidence, uuid.UUID(fact["id"]))
                    row.stance, row.supports_hypothesis_id = stance[fact["label"]], hypothesis_id
            hypothesis = await self.session.get(Hypothesis, hypothesis_id)
            hypothesis.verdict, hypothesis.verifier_notes = Verdict(v.verdict), v.notes
            await self.session.flush()

            again = v.verdict == Verdict.WEAK and state["round"] < MAX_ROUNDS
            await self.bus.publish(
                VerdictReached(
                    hypothesis_id=hypothesis_id,
                    verdict=v.verdict,
                    notes=v.notes,
                    gaps=v.gaps,
                    reinvestigating=again,
                )
            )
            info["summary"] = f"{v.verdict}: {v.notes[:160]}"
        return {
            "facts": facts,
            "verdict": {
                "hypothesis_id": top["id"],
                "verdict": v.verdict,
                "notes": v.notes,
                "gaps": v.gaps,
            },
        }

    async def score_confidence(self, state: AgentState) -> AgentState:
        async with self.step(Step.SCORE_CONFIDENCE) as info:
            parts = components(state["facts"], Verdict(state["verdict"]["verdict"]))
            value = score(parts)
            contradictions = [
                uuid.UUID(f["id"]) for f in state["facts"] if f["stance"] == Stance.CONTRADICTS
            ]
            self.inv.confidence = value
            self.inv.confidence_breakdown = {p.name: p.value for p in parts}
            await self.bus.publish(
                ConfidenceScored(score=value, components=parts, contradiction_ids=contradictions)
            )
            info["summary"] = f"{value:.0%}"
        return {"confidence": value}

    async def write_report(self, state: AgentState) -> AgentState:
        verdict = state["verdict"]
        async with self.step(Step.WRITE_REPORT, "ultra") as info:
            r = await self.ask(
                "ultra",
                prompts.REPORT.format(
                    question=self.inv.question,
                    hypothesis=state["hypotheses"][0]["statement"],
                    verdict=verdict["verdict"],
                    notes=verdict["notes"],
                    confidence=state["confidence"],
                    fix=FIX_SUMMARY[state.get("fix", "none")],
                    facts=_fact_lines(state["facts"]),
                    inferences=_inference_lines(state.get("inferences", [])),
                ),
                ReportOut,
                "report",
                max_tokens=4000,
            )
            # No root cause when the hypothesis was refuted, or when nothing is broken.
            refuted = verdict["verdict"] == Verdict.REFUTED
            root_cause = None if refuted or state.get("no_fault") else r.root_cause
            payload = ReportReady(
                root_cause=root_cause,
                summary=r.summary,
                verdict=verdict["verdict"],
                confidence=state["confidence"],
                fact_ids=[uuid.UUID(f["id"]) for f in state["facts"]],
                inference_ids=[uuid.UUID(i["id"]) for i in state.get("inferences", [])],
                contradiction_ids=[
                    uuid.UUID(f["id"]) for f in state["facts"] if f["stance"] == Stance.CONTRADICTS
                ],
                fix=REPORT_FIX[state.get("fix", "none")],
                patch_id=uuid.UUID(state["patch"]["id"]) if state.get("patch") else None,
                next_steps=r.next_steps,
            )
            self.inv.root_cause = root_cause
            # deployed_ref: which version was investigated; opening the PR needs it later.
            self.inv.report = {**payload.model_dump(mode="json"), "deployed_ref": self.ctx.ref}
            await self.bus.publish(payload)
            info["summary"] = (root_cause or "no root cause established")[:200]
        return {"report": payload.model_dump(mode="json")}

    # fix generator ----------------------------------------------------------------------------

    def workspace(self) -> Path:
        """A snapshot of the deployed code, made once per investigation."""
        if self.base is None:
            self.tmp = Path(tempfile.mkdtemp(prefix="roottrace-"))
            self.base = self.tmp / "base"
            fix.snapshot(self.ctx.repo_path, self.ctx.ref, self.base)
        return self.base

    def copy_with(self, name: str, changes: list[fix.FileChange]) -> Path:
        target = self.tmp / name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(self.base, target)
        fix.apply(target, changes)
        return target

    async def generate_patch(self, state: AgentState) -> AgentState:
        attempt = state.get("attempt", 0) + 1
        tier = "super" if attempt == 1 else "ultra"  # escalate on the second attempt
        async with self.step(Step.GENERATE_PATCH, tier, attempt) as info:
            base = self.workspace()
            paths = fix.involved_files(state["facts"], self.ctx.repo_path, self.ctx.ref)
            feedback = state.get("fix_feedback")
            prompt = prompts.GENERATE_PATCH.format(
                question=self.inv.question,
                hypothesis=state["hypotheses"][0]["statement"],
                facts=_fact_lines(state["facts"]),
                ref=self.ctx.ref,
                files=fix.read_files(base, paths),
                test_files=", ".join(fix.test_files(base)) or "(none)",
                example_test=fix.example_test(base, paths),
                test_command=self.test_command,
                feedback=prompts.PATCH_FEEDBACK.format(feedback=feedback) if feedback else "",
            )
            out = await self.ask(tier, prompt, fix.PatchOut, "generate_patch", max_tokens=12000)
            files = [out.test, *out.changes]
            diff = fix.unified_diff(base, files)
            row = Patch(
                investigation_id=self.inv.id,
                attempt=attempt,
                diff=diff,
                files_changed=[{"path": f.path, "explanation": f.explanation} for f in files],
                explanation=out.explanation,
            )
            self.session.add(row)
            await self.session.flush()
            await self.bus.publish(
                PatchProposed(
                    patch_id=row.id,
                    attempt=attempt,
                    diff=diff,
                    files=[PatchFile(path=f.path, explanation=f.explanation) for f in files],
                    explanation=out.explanation,
                )
            )
            update: AgentState = {
                "attempt": attempt,
                "patch": {"id": str(row.id), **out.model_dump(), "diff": diff},
                "blocked": False,
            }
            blocked = fix.touches_existing_tests(base, files)
            if blocked:
                feedback = (
                    f"Rejected before testing: the patch changes existing test file(s) "
                    f"{', '.join(blocked)}. Existing tests may never be edited or deleted. "
                    "Put your regression test in a NEW file under tests/."
                )
                row.status = PatchStatus.REJECTED
                await self.bus.publish(
                    PatchStatusChanged(patch_id=row.id, status=PatchStatus.REJECTED, notes=feedback)
                )
                update |= {
                    "blocked": True,
                    "fix": PatchStatus.REJECTED.value,
                    "fix_feedback": feedback,
                }
                info["summary"] = f"rejected: edits existing tests {blocked}"
            else:
                info["summary"] = f"{len(out.changes)} file(s) changed, test {out.test.path}"
        return update

    async def run_one(self, patch_id: uuid.UUID, phase: TestPhase, workdir: Path, command: str):
        result = await self.sandbox.run(self.image, workdir, command)
        # Baseline must FAIL a test (pytest exit 1), not crash on import (exit 2).
        expected = result.exit_code == (1 if phase == TestPhase.BASELINE else 0)
        row = TestRun(
            patch_id=patch_id,
            phase=phase,
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            passed=result.passed,
            failed=result.failed,
            duration_ms=result.duration_ms,
            sandbox_image_id=result.operation_id,
        )
        self.session.add(row)
        await self.session.flush()
        for line in (result.stdout + result.stderr).splitlines()[-TEST_OUTPUT_LINES:]:
            await self.bus.publish(TestOutput(patch_id=patch_id, phase=phase, line=line))
        await self.bus.publish(
            TestCompleted(
                test_run_id=row.id,
                patch_id=patch_id,
                phase=phase,
                command=command,
                exit_code=result.exit_code,
                passed=result.passed,
                failed=result.failed,
                duration_ms=result.duration_ms,
                sandbox_image_id=result.operation_id,
                as_expected=expected,
            )
        )
        return result, expected

    async def run_tests(self, state: AgentState) -> AgentState:
        patch = state["patch"]
        patch_id = uuid.UUID(patch["id"])
        test = fix.FileChange(**patch["test"])
        changes = [fix.FileChange(**c) for c in patch["changes"]]
        async with self.step(Step.RUN_TESTS, None, state["attempt"]) as info:
            try:
                if self.image is None:
                    self.image = await self.sandbox.environment(
                        self.workspace(), self.setup_command
                    )
                baseline, red = await self.run_one(
                    patch_id,
                    TestPhase.BASELINE,
                    self.copy_with("baseline", [test]),
                    f"{self.test_command} {test.path}",
                )
                patched, green = await self.run_one(
                    patch_id,
                    TestPhase.PATCHED,
                    self.copy_with("patched", [test, *changes]),
                    self.test_command,
                )
            except (SandboxError, ToolError, OSError) as exc:
                # Investigation-only mode: the fix stands, but it is not tested.
                await self.bus.publish(
                    ErrorRaised(
                        message=f"sandbox unavailable: {exc}"[:500],
                        step=Step.RUN_TESTS,
                        fatal=False,
                    )
                )
                info["summary"] = "tests could not run: patch is untested"
                return {"tests": None}
            info["summary"] = (
                f"baseline {'failed as expected' if red else 'did not reproduce the bug'}; "
                f"patched {'passed' if green else 'failed'}"
            )
        tests = {
            phase: {
                "exit": r.exit_code,
                "passed": r.passed,
                "failed": r.failed,
                "ok": ok,
                "tail": (r.stdout + r.stderr)[-FEEDBACK_CHARS:],
            }
            for phase, r, ok in (("baseline", baseline, red), ("patched", patched, green))
        }
        return {"tests": tests}

    async def verify_fix(self, state: AgentState) -> AgentState:
        patch_id = uuid.UUID(state["patch"]["id"])
        tests = state.get("tests")
        async with self.step(Step.VERIFY_FIX, "ultra", state["attempt"]) as info:
            feedback = None
            if tests is None:
                status, notes = PatchStatus.UNTESTED, "Tests could not run in the sandbox."
            elif not tests["baseline"]["ok"]:
                status = PatchStatus.TESTS_FAILED
                notes = feedback = (
                    "The new test did not fail on the current code (pytest exit "
                    f"{tests['baseline']['exit']}; it must exit 1 with a failing assertion, not "
                    "an import or collection error), so it does not reproduce the bug. The test "
                    "may only use code that already exists.\n" + tests["baseline"]["tail"]
                )
            elif not tests["patched"]["ok"]:
                status = PatchStatus.TESTS_FAILED
                notes = feedback = (
                    "The test suite failed with the fix:\n" + tests["patched"]["tail"]
                )
            else:
                judgement = await self.ask(
                    "ultra",
                    prompts.VERIFY_FIX.format(
                        question=self.inv.question,
                        hypothesis=state["hypotheses"][0]["statement"],
                        explanation=state["patch"]["explanation"],
                        diff=state["patch"]["diff"][: FEEDBACK_CHARS * 2],
                        baseline_exit=tests["baseline"]["exit"],
                        baseline_summary=_counts(tests["baseline"]),
                        patched_exit=tests["patched"]["exit"],
                        patched_summary=_counts(tests["patched"]),
                    ),
                    fix.FixJudgement,
                    "verify_fix",
                )
                notes = judgement.notes
                if judgement.addresses_root_cause:
                    status = PatchStatus.VERIFIED
                else:
                    status, feedback = PatchStatus.REJECTED, judgement.notes

            row = await self.session.get(Patch, patch_id)
            row.status = status
            await self.bus.publish(
                PatchStatusChanged(patch_id=patch_id, status=status, notes=notes)
            )

            update: AgentState = {"fix": status.value, "fix_feedback": feedback}
            if status == PatchStatus.VERIFIED:  # the last 20% of the confidence score
                parts = components(state["facts"], Verdict(state["verdict"]["verdict"]), True)
                update["confidence"] = self.inv.confidence = score(parts)
                self.inv.confidence_breakdown = {p.name: p.value for p in parts}
                await self.bus.publish(
                    ConfidenceScored(score=update["confidence"], components=parts)
                )
            info["summary"] = f"{status.value}: {notes[:160]}"
        return update

    async def draft_pr(self, state: AgentState) -> AgentState:
        patch_id = uuid.UUID(state["patch"]["id"])
        async with self.step(Step.DRAFT_PR, "super") as info:
            pr = await self.ask(
                "super",
                prompts.DRAFT_PR.format(
                    question=self.inv.question,
                    root_cause=state["report"]["root_cause"],
                    explanation=state["patch"]["explanation"],
                    diff=state["patch"]["diff"][: FEEDBACK_CHARS * 2],
                    passed=state["tests"]["patched"]["passed"],
                    facts=_fact_lines(state["facts"]),
                ),
                fix.PrOut,
                "draft_pr",
            )
            row = await self.session.get(Patch, patch_id)
            row.pr_title, row.pr_body = pr.title, pr.body
            await self.bus.publish(PrDrafted(patch_id=patch_id, title=pr.title, body=pr.body))
            info["summary"] = pr.title
        return {}

    async def save_memories(self, state: AgentState) -> AgentState:
        # Only a verified root cause becomes a lesson: saving a guess would teach a mistake.
        async with self.step(Step.SAVE_MEMORIES, "nano") as info:
            if state["verdict"]["verdict"] != Verdict.SUPPORTED or state.get("no_fault"):
                info["summary"] = "nothing saved: the root cause was not verified"
                return {}
            try:
                lessons = await self.ask(
                    "nano",
                    memory.SAVE_PROMPT.format(
                        question=self.inv.question,
                        root_cause=state["report"]["root_cause"],
                        fix=FIX_SUMMARY[state.get("fix", "none")],
                        facts=_fact_lines(state["facts"]),
                    ),
                    memory.Lessons,
                    "save_memories",
                )
                saved = await memory.save_lessons(
                    self.session, self.router, self.inv.project_id, self.inv.id, lessons.lessons
                )
            except Exception as exc:  # noqa: BLE001 - a lost lesson must not fail the investigation
                await self.bus.publish(
                    ErrorRaised(
                        message=f"saving memories failed: {exc}"[:500],
                        step=Step.SAVE_MEMORIES,
                        fatal=False,
                    )
                )
                info["summary"] = "no memories saved"
                return {}
            info["summary"] = f"{len(saved)} new lesson(s) saved"
        return {}

    # run --------------------------------------------------------------------------------------

    async def run(self) -> AgentState | None:
        start = time.perf_counter()
        self.inv.status = InvestigationStatus.RUNNING
        await self.bus.publish(StatusChanged(status=InvestigationStatus.RUNNING))
        state = None
        try:
            state = await self.graph.ainvoke(
                {"round": 0, "facts": [], "inferences": []}, {"recursion_limit": 30}
            )
            status = InvestigationStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001 - recorded and reported, never swallowed
            await self.session.rollback()
            await self.bus.publish(
                ErrorRaised(message=f"{type(exc).__name__}: {exc}"[:2000], fatal=True)
            )
            status = InvestigationStatus.FAILED
        finally:
            if self.tmp:
                shutil.rmtree(self.tmp, ignore_errors=True)

        self.save_trace()
        tokens = sum(s["prompt_tokens"] + s["completion_tokens"] for s in self.router.spans)
        cost = sum(s["cost_usd"] for s in self.router.spans)
        # A drafted PR waits for an engineer: the run is done, the investigation is not.
        awaiting = state is not None and state.get("fix") == PatchStatus.VERIFIED
        final = InvestigationStatus.AWAITING_APPROVAL if awaiting else status
        self.inv.status, self.inv.finished_at = final, datetime.now(UTC)
        self.inv.total_tokens, self.inv.total_cost_usd = tokens, round(cost, 6)
        await self.bus.publish(StatusChanged(status=final))
        await self.bus.publish(
            Done(
                status=status,
                duration_ms=int((time.perf_counter() - start) * 1000),
                total_tokens=tokens,
                total_cost_usd=round(cost, 6),
            )
        )
        return state

    def save_trace(self) -> None:
        """Every model and tool call becomes a trace span (the Trace tab)."""
        for s in self.router.spans:
            tier = s.get("tier")
            self.session.add(
                TraceSpan(
                    investigation_id=self.inv.id,
                    kind=SpanKind.RETRIEVAL if tier == "embed" else SpanKind.LLM,
                    name=s["name"],
                    model=s["model"],
                    tier=ModelTier(tier) if tier in ModelTier._value2member_map_ else None,
                    prompt_version=prompts.PROMPT_VERSION,
                    prompt_tokens=s["prompt_tokens"],
                    completion_tokens=s["completion_tokens"],
                    cost_usd=s["cost_usd"],
                    latency_ms=s["latency_ms"],
                    cached=s.get("cached", False),
                    downgraded=s.get("downgraded", False),
                )
            )
        for t in self.ctx.trace:
            self.session.add(
                TraceSpan(
                    investigation_id=self.inv.id,
                    kind=SpanKind.TOOL,
                    name=t["tool"],
                    input={"arguments": t["arguments"]},
                    latency_ms=t["latency_ms"],
                    status=SpanStatus.OK if t["ok"] else SpanStatus.ERROR,
                )
            )
