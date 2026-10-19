"""The investigation event stream: what the agent emits and the Investigation page renders.

DRAFT v1, pending Person A's review. Changing a payload is a contract change: update this file,
regenerate the frontend types (`npm run gen:api`), and tell the other person.

Every event is an envelope, stored in `investigation_events` and streamed over SSE:

    id: 12
    event: step_started
    data: {"seq": 12, "ts": "2026-10-21T14:02:03Z", "type": "step_started",
           "payload": {"step": "investigate", "round": 1, "tier": "super", "model": "..."}}

`seq` counts from 1 per investigation and is the SSE `id`, so a client resumes with
`Last-Event-ID`. The agent never builds envelopes itself: it publishes a payload, e.g.
`StepStarted(step=Step.PLAN, tier=ModelTier.SUPER, model=...)`, and `make_event` wraps it.

Types marked "(added)" are not in the plan's original list (IMPLEMENTATION_PLAN.md §7); each
one feeds a part of the Investigation page that had no event to drive it.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.models import (
    EvidenceKind,
    EvidenceSourceType,
    InvestigationStatus,
    ModelTier,
    PatchStatus,
    Stance,
    TestPhase,
    Verdict,
)


class Step(StrEnum):
    """LangGraph nodes, in run order (diagram 02). The timeline shows all of them from the start."""

    TRIAGE = "triage"
    RECALL_MEMORY = "recall_memory"
    PLAN = "plan"
    INVESTIGATE = "investigate"
    COMPARE_EVIDENCE = "compare_evidence"
    HYPOTHESIZE = "hypothesize"
    VERIFY = "verify"
    SCORE_CONFIDENCE = "score_confidence"
    GENERATE_PATCH = "generate_patch"
    RUN_TESTS = "run_tests"
    VERIFY_FIX = "verify_fix"
    WRITE_REPORT = "write_report"
    DRAFT_PR = "draft_pr"
    SAVE_MEMORIES = "save_memories"
    AWAIT_APPROVAL = "await_approval"
    CREATE_PR = "create_pr"


class _Schema(BaseModel):
    model_config = ConfigDict(
        # A misspelled field in the agent fails when published, not silently in the UI.
        extra="forbid",
        # Fields with defaults are still always sent, so the generated TypeScript types mark
        # them required (`round: number`, not `round?: number`).
        json_schema_serialization_defaults_required=True,
    )


class EventPayload(_Schema):
    pass


# --- Steps -----------------------------------------------------------------------------------


class StepStarted(EventPayload):
    step: Step
    round: int = Field(default=1, ge=1, description="2 when re-investigating after a weak verdict")
    tier: ModelTier | None = Field(default=None, description="None for steps with no LLM call")
    model: str | None = None


class StepCompleted(EventPayload):
    step: Step
    round: int = Field(default=1, ge=1)
    duration_ms: int = Field(ge=0)
    summary: str | None = Field(default=None, description='One line, e.g. "5 steps planned"')
    skipped: bool = Field(
        default=False,
        description="True for steps that did not run, e.g. the fix steps after a refuted verdict."
        " May arrive without a step_started.",
    )


# --- Tool calls ------------------------------------------------------------------------------


class ToolCall(EventPayload):
    call_id: str = Field(description="Pairs this call with its tool_result")
    step: Step
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    transport: Literal["direct", "mcp"] = "direct"


class ToolResult(EventPayload):
    call_id: str
    tool: str
    ok: bool
    duration_ms: int = Field(ge=0)
    summary: str = Field(description='One line for the feed, e.g. "23 errors on 2 routes"')
    output_preview: str = Field(default="", description="Truncated output for the expanded view")
    error: str | None = None


# --- Reasoning -------------------------------------------------------------------------------


class EvidenceAdded(EventPayload):
    evidence_id: uuid.UUID
    label: str = Field(description='Handle the report cites: "F1" for facts, "I1" for inferences')
    kind: EvidenceKind
    statement: str
    tool: str | None = None
    source_type: EvidenceSourceType | None = None
    source_ref: str | None = Field(default=None, description='e.g. "commit:a3f9c21"')
    excerpt: str | None = Field(default=None, description="Verbatim text from the source")
    cites: list[uuid.UUID] = Field(default_factory=list, description="Facts an inference uses")
    stance: Stance = Stance.NEUTRAL
    weight: float = Field(default=1.0, ge=0)
    hypothesis_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _facts_are_cited(self) -> Self:
        # Same rule as the evidence table's ck_evidence_fact_is_cited constraint.
        if self.kind is EvidenceKind.FACT and None in (
            self.source_type,
            self.source_ref,
            self.excerpt,
        ):
            raise ValueError("a fact needs source_type, source_ref and excerpt")
        return self


class HypothesisProposed(EventPayload):
    hypothesis_id: uuid.UUID
    statement: str
    rank: int = Field(default=1, ge=1)
    round: int = Field(default=1, ge=1)


class VerdictReached(EventPayload):
    hypothesis_id: uuid.UUID
    verdict: Verdict
    notes: str | None = None
    gaps: list[str] = Field(default_factory=list, description="What a re-investigation targets")
    reinvestigating: bool = Field(
        default=False, description="Weak verdict with a round left: the agent loops back"
    )


class ConfidenceComponent(_Schema):
    name: Literal["coverage", "strength", "verifier", "history", "fix", "contradiction"]
    value: float = Field(ge=0, le=1)
    weight: float = Field(description="Negative for contradiction")


class ConfidenceScored(EventPayload):
    """Sent after scoring, and again once the fix is verified. The latest one wins."""

    score: float = Field(ge=0, le=1)
    components: list[ConfidenceComponent]
    contradiction_ids: list[uuid.UUID] = Field(default_factory=list)


# --- Fix Generator ---------------------------------------------------------------------------


class PatchFile(_Schema):
    path: str
    explanation: str = ""


class PatchProposed(EventPayload):
    patch_id: uuid.UUID
    attempt: int = Field(ge=1)
    diff: str = Field(description="Unified diff")
    files: list[PatchFile]
    explanation: str | None = None


class TestOutput(EventPayload):
    __test__ = False  # not a pytest test class

    patch_id: uuid.UUID
    phase: TestPhase
    stream: Literal["stdout", "stderr"] = "stdout"
    line: str


class TestCompleted(EventPayload):
    """(added) The Tests tab's result row: counts, duration, and proof it ran on Nebius."""

    __test__ = False  # not a pytest test class

    test_run_id: uuid.UUID
    patch_id: uuid.UUID
    phase: TestPhase
    command: str
    exit_code: int | None = Field(description="None if the run timed out")
    passed: int | None = None
    failed: int | None = None
    duration_ms: int = Field(ge=0)
    sandbox_image_id: str | None = None
    as_expected: bool = Field(description="Baseline failed (bug reproduced) / patched passed")


class PatchStatusChanged(EventPayload):
    """(added) verify_fix's outcome, or "untested" in investigation-only mode."""

    patch_id: uuid.UUID
    status: PatchStatus
    notes: str | None = None


class PrDrafted(EventPayload):
    """(added) Fills the PR draft tab."""

    patch_id: uuid.UUID
    title: str
    body: str = Field(description="Markdown")


class PrOpened(EventPayload):
    """(added) After the engineer approved and the draft PR was created."""

    patch_id: uuid.UUID
    url: str


# --- Outcome ---------------------------------------------------------------------------------


class ReportReady(EventPayload):
    root_cause: str | None = Field(description="None when no root cause was established")
    summary: str = Field(description='Markdown; cites evidence by label, e.g. "[F1]"')
    verdict: Verdict | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    fact_ids: list[uuid.UUID] = Field(default_factory=list)
    inference_ids: list[uuid.UUID] = Field(default_factory=list)
    contradiction_ids: list[uuid.UUID] = Field(default_factory=list)
    fix: Literal["verified", "untested", "failed", "none"] = Field(
        description='"none": no safe automated fix was found'
    )
    patch_id: uuid.UUID | None = None
    next_steps: list[str] = Field(default_factory=list)


class StatusChanged(EventPayload):
    """(added) Every investigation status change, including the pause for PR approval."""

    status: InvestigationStatus


class ErrorRaised(EventPayload):
    message: str
    step: Step | None = None
    fatal: bool = Field(description="False: the agent carries on, e.g. after one tool failed")


class Done(EventPayload):
    """Last event of a run; the SSE stream closes after it."""

    status: Literal[InvestigationStatus.COMPLETED, InvestigationStatus.FAILED]
    duration_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0)


# --- Follow-up chat --------------------------------------------------------------------------


class MessageDelta(EventPayload):
    """(added) A streamed piece of a follow-up chat reply."""

    message_id: uuid.UUID
    delta: str


class MessageCompleted(EventPayload):
    """(added) The full follow-up reply, once streaming ends."""

    message_id: uuid.UUID
    content: str
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)


# --- Envelopes -------------------------------------------------------------------------------


class _Envelope(_Schema):
    seq: int = Field(ge=1, description="Per-investigation order from 1; also the SSE id")
    ts: datetime


class StepStartedEvent(_Envelope):
    type: Literal["step_started"]
    payload: StepStarted


class StepCompletedEvent(_Envelope):
    type: Literal["step_completed"]
    payload: StepCompleted


class ToolCallEvent(_Envelope):
    type: Literal["tool_call"]
    payload: ToolCall


class ToolResultEvent(_Envelope):
    type: Literal["tool_result"]
    payload: ToolResult


class EvidenceAddedEvent(_Envelope):
    type: Literal["evidence_added"]
    payload: EvidenceAdded


class HypothesisEvent(_Envelope):
    type: Literal["hypothesis"]
    payload: HypothesisProposed


class VerdictEvent(_Envelope):
    type: Literal["verdict"]
    payload: VerdictReached


class ConfidenceEvent(_Envelope):
    type: Literal["confidence"]
    payload: ConfidenceScored


class PatchProposedEvent(_Envelope):
    type: Literal["patch_proposed"]
    payload: PatchProposed


class TestOutputEvent(_Envelope):
    __test__ = False  # not a pytest test class

    type: Literal["test_output"]
    payload: TestOutput


class TestCompletedEvent(_Envelope):
    __test__ = False  # not a pytest test class

    type: Literal["test_completed"]
    payload: TestCompleted


class PatchStatusEvent(_Envelope):
    type: Literal["patch_status"]
    payload: PatchStatusChanged


class PrDraftEvent(_Envelope):
    type: Literal["pr_draft"]
    payload: PrDrafted


class PrOpenedEvent(_Envelope):
    type: Literal["pr_opened"]
    payload: PrOpened


class ReportEvent(_Envelope):
    type: Literal["report"]
    payload: ReportReady


class StatusEvent(_Envelope):
    type: Literal["status"]
    payload: StatusChanged


class ErrorEvent(_Envelope):
    type: Literal["error"]
    payload: ErrorRaised


class DoneEvent(_Envelope):
    type: Literal["done"]
    payload: Done


class MessageDeltaEvent(_Envelope):
    type: Literal["message_delta"]
    payload: MessageDelta


class MessageEvent(_Envelope):
    type: Literal["message"]
    payload: MessageCompleted


EVENT_CLASSES: tuple[type[_Envelope], ...] = (
    StepStartedEvent,
    StepCompletedEvent,
    ToolCallEvent,
    ToolResultEvent,
    EvidenceAddedEvent,
    HypothesisEvent,
    VerdictEvent,
    ConfidenceEvent,
    PatchProposedEvent,
    TestOutputEvent,
    TestCompletedEvent,
    PatchStatusEvent,
    PrDraftEvent,
    PrOpenedEvent,
    ReportEvent,
    StatusEvent,
    ErrorEvent,
    DoneEvent,
    MessageDeltaEvent,
    MessageEvent,
)

# Any one event; parse with `event_adapter.validate_json(...)`.
Event = Annotated[
    StepStartedEvent
    | StepCompletedEvent
    | ToolCallEvent
    | ToolResultEvent
    | EvidenceAddedEvent
    | HypothesisEvent
    | VerdictEvent
    | ConfidenceEvent
    | PatchProposedEvent
    | TestOutputEvent
    | TestCompletedEvent
    | PatchStatusEvent
    | PrDraftEvent
    | PrOpenedEvent
    | ReportEvent
    | StatusEvent
    | ErrorEvent
    | DoneEvent
    | MessageDeltaEvent
    | MessageEvent,
    Field(discriminator="type"),
]
event_adapter: TypeAdapter[Event] = TypeAdapter(Event)

# payload class -> (envelope class, wire type),
# e.g. StepStarted -> (StepStartedEvent, "step_started")
_ENVELOPE_FOR: dict[type[EventPayload], tuple[type[_Envelope], str]] = {
    cls.model_fields["payload"].annotation: (cls, get_args(cls.model_fields["type"].annotation)[0])
    for cls in EVENT_CLASSES
}
EVENT_TYPES: tuple[str, ...] = tuple(wire_type for _, wire_type in _ENVELOPE_FOR.values())


def make_event(seq: int, payload: EventPayload, ts: datetime | None = None) -> Event:
    """Wrap a payload in its envelope. The event bus calls this; the agent only builds payloads."""
    cls, wire_type = _ENVELOPE_FOR[type(payload)]
    return cls(seq=seq, ts=ts or datetime.now(UTC), type=wire_type, payload=payload)


def sse_frame(event: _Envelope) -> str:
    """One Server-Sent Events frame. `id` lets a reconnecting client send Last-Event-ID."""
    return f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
