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
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agent import prompts
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
    Memory,
    ModelTier,
    SpanKind,
    SpanStatus,
    Stance,
    TraceSpan,
    Verdict,
)
from app.schemas.events import (
    ConfidenceScored,
    Done,
    ErrorRaised,
    EvidenceAdded,
    HypothesisProposed,
    ReportReady,
    StatusChanged,
    Step,
    StepCompleted,
    StepStarted,
    ToolCall,
    ToolResult,
    VerdictReached,
)
from app.tools import TOOLS, ToolContext, call_tool, openai_schemas

MAX_ROUNDS = 2  # investigate at most twice
INVESTIGATE_BUDGET = 12  # tool calls per investigate round
VERIFY_BUDGET = 4  # tool calls the verifier may use to look for contradictions
FACTS_PER_ROUND = 10
PREVIEW_CHARS = 1500

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


# --- The agent --------------------------------------------------------------------------------


class Investigator:
    def __init__(
        self,
        investigation: Investigation,
        tools: ToolContext,
        bus: EventBus,
        reported_at: datetime | None = None,
    ):
        self.inv = investigation
        self.ctx = tools
        self.session = tools.session
        self.router: LLMRouter = tools.router
        self.bus = bus
        self.reported_at = (reported_at or datetime.now(UTC)).isoformat(timespec="minutes")
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
            "write_report",
        ):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "triage")
        g.add_edge("triage", "recall_memory")
        g.add_edge("recall_memory", "plan")
        g.add_edge("plan", "investigate")
        g.add_edge("investigate", "compare_evidence")
        g.add_edge("compare_evidence", "hypothesize")
        g.add_edge("hypothesize", "verify")
        g.add_conditional_edges("verify", self.after_verify, ["investigate", "score_confidence"])
        g.add_edge("score_confidence", "write_report")
        g.add_edge("write_report", END)
        return g.compile()

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
        """Let the model call tools until it stops or the budget runs out (a ReAct loop)."""
        messages = [
            {"role": "system", "content": prompts.SYSTEM},
            {"role": "user", "content": prompt},
        ]
        transcript: list[dict] = []
        schemas = openai_schemas()
        while len(transcript) < budget:
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
                if len(transcript) >= budget:
                    output = json.dumps({"error": "tool budget used up; summarise what you found"})
                else:
                    output = await self.call(step, call)
                    transcript.append(
                        {
                            "n": len(transcript) + 1,
                            "tool": call["name"],
                            "arguments": call["arguments"],
                            "output": output,
                        }
                    )
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
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
        text = "\n\n".join(
            f"[call {t['n']}] {t['tool']}({t['arguments']})\n{t['output']}" for t in transcript
        )
        found = await self.ask(
            "super",
            prompts.EXTRACT_FACTS.format(
                question=self.inv.question,
                transcript=text,
                limit=FACTS_PER_ROUND,
                known=_fact_lines(state.get("facts", [])),
            ),
            FactList,
            "extract_facts",
            max_tokens=4000,
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
        # Long-term memory recall arrives in Phase 7; for now report what exists.
        async with self.step(Step.RECALL_MEMORY) as info:
            count = await self.session.scalar(
                select(func.count())
                .select_from(Memory)
                .where(Memory.project_id == self.inv.project_id)
            )
            info["summary"] = f"{count} memories recalled"
        return {}

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
                ),
                Plan,
                "plan",
            )
            info["summary"] = f"{len(p.steps)} steps planned"
        return {"plan": p.steps}

    async def investigate(self, state: AgentState) -> AgentState:
        rnd = state.get("round", 1) + (1 if "verdict" in state else 0)
        async with self.step(Step.INVESTIGATE, "super", rnd) as info:
            focus = ""
            if rnd > 1:
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
        return {"hypotheses": hypotheses}

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
                    facts=_fact_lines(state["facts"]),
                    inferences=_inference_lines(state.get("inferences", [])),
                ),
                ReportOut,
                "report",
                max_tokens=4000,
            )
            root_cause = r.root_cause if verdict["verdict"] != Verdict.REFUTED else None
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
                fix="none",  # the Fix Generator (Phase 6) replaces this
                next_steps=r.next_steps,
            )
            self.inv.root_cause = root_cause
            self.inv.report = payload.model_dump(mode="json")
            await self.bus.publish(payload)
            info["summary"] = (root_cause or "no root cause established")[:200]
        return {"report": payload.model_dump(mode="json")}

    # run --------------------------------------------------------------------------------------

    async def run(self) -> AgentState | None:
        start = time.perf_counter()
        self.inv.status = InvestigationStatus.RUNNING
        await self.bus.publish(StatusChanged(status=InvestigationStatus.RUNNING))
        state = None
        try:
            state = await self.graph.ainvoke(
                {"round": 1, "facts": [], "inferences": []}, {"recursion_limit": 30}
            )
            status = InvestigationStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001 - recorded and reported, never swallowed
            await self.session.rollback()
            await self.bus.publish(
                ErrorRaised(message=f"{type(exc).__name__}: {exc}"[:2000], fatal=True)
            )
            status = InvestigationStatus.FAILED

        self.save_trace()
        tokens = sum(s["prompt_tokens"] + s["completion_tokens"] for s in self.router.spans)
        cost = sum(s["cost_usd"] for s in self.router.spans)
        self.inv.status, self.inv.finished_at = status, datetime.now(UTC)
        self.inv.total_tokens, self.inv.total_cost_usd = tokens, round(cost, 6)
        await self.bus.publish(StatusChanged(status=status))
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
