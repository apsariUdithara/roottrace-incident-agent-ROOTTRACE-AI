"""The event contract between the agent (Person A) and the Investigation page (Person B)."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models import EvidenceKind, EvidenceSourceType, InvestigationStatus, ModelTier, Verdict
from app.schemas.events import (
    EVENT_CLASSES,
    EVENT_TYPES,
    ConfidenceComponent,
    ConfidenceScored,
    Done,
    EvidenceAdded,
    PatchFile,
    PatchProposed,
    Step,
    StepStarted,
    StepStartedEvent,
    ToolCall,
    VerdictReached,
    event_adapter,
    make_event,
    sse_frame,
)

# IMPLEMENTATION_PLAN.md §7, plus the ones this contract adds for the UI.
PLANNED = {
    "step_started", "step_completed", "tool_call", "tool_result", "evidence_added", "hypothesis",
    "verdict", "patch_proposed", "test_output", "confidence", "report", "error", "done",
}  # fmt: skip
ADDED = {
    "test_completed", "patch_status", "pr_draft", "pr_opened", "status", "message_delta", "message",
}  # fmt: skip
TS = datetime(2026, 10, 21, 14, 2, 3, tzinfo=UTC)


def test_event_types_are_the_agreed_set() -> None:
    assert set(EVENT_TYPES) == PLANNED | ADDED
    assert len(EVENT_TYPES) == len(set(EVENT_TYPES)) == len(EVENT_CLASSES)


def test_wire_format() -> None:
    event = make_event(
        12, StepStarted(step=Step.INVESTIGATE, tier=ModelTier.SUPER, model="nemotron"), ts=TS
    )

    assert isinstance(event, StepStartedEvent)
    assert json.loads(event.model_dump_json()) == {
        "seq": 12,
        "ts": "2026-10-21T14:02:03Z",
        "type": "step_started",
        "payload": {"step": "investigate", "round": 1, "tier": "super", "model": "nemotron"},
    }


def test_sse_frame_keeps_data_on_one_line() -> None:
    patch = PatchProposed(
        patch_id=uuid.uuid4(),
        attempt=1,
        diff="--- a/config.py\n+++ b/config.py\n-POOL = 100\n+POOL = 20\n",
        files=[PatchFile(path="config.py", explanation="Restore the limit")],
    )

    frame = sse_frame(make_event(7, patch, ts=TS))

    assert frame.startswith("id: 7\nevent: patch_proposed\ndata: {")
    assert frame.endswith("}\n\n")
    assert frame.count("\n") == 4  # the diff's newlines are escaped inside the JSON


def test_json_round_trip_keeps_the_payload_type() -> None:
    original = make_event(
        3,
        VerdictReached(
            hypothesis_id=uuid.uuid4(),
            verdict=Verdict.WEAK,
            gaps=["check DB CPU during the window"],
            reinvestigating=True,
        ),
    )

    parsed = event_adapter.validate_json(original.model_dump_json())

    assert parsed == original
    assert isinstance(parsed.payload, VerdictReached)


def test_facts_must_be_cited_but_inferences_cite_facts() -> None:
    with pytest.raises(ValidationError, match="a fact needs source_type, source_ref and excerpt"):
        EvidenceAdded(
            evidence_id=uuid.uuid4(), label="F1", kind=EvidenceKind.FACT, statement="23 errors"
        )

    fact = EvidenceAdded(
        evidence_id=uuid.uuid4(),
        label="F1",
        kind=EvidenceKind.FACT,
        statement="23 OperationalError: too many connections",
        tool="search_logs",
        source_type=EvidenceSourceType.LOG,
        source_ref="log:4bf92f35",
        excerpt="OperationalError: too many connections",
    )
    inference = EvidenceAdded(
        evidence_id=uuid.uuid4(),
        label="I1",
        kind=EvidenceKind.INFERENCE,
        statement="Pool exhaustion causes the 500s",
        cites=[fact.evidence_id],
    )
    assert inference.source_ref is None


def test_payloads_reject_misspelled_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ToolCall(call_id="c1", step=Step.INVESTIGATE, tool="search_logs", arguemnts={})


def test_values_are_range_checked() -> None:
    with pytest.raises(ValidationError):
        ConfidenceScored(score=1.2, components=[])
    with pytest.raises(ValidationError):
        ConfidenceComponent(name="vibes", value=0.5, weight=0.1)
    with pytest.raises(ValidationError):  # a run only ends completed or failed
        Done(status=InvestigationStatus.RUNNING, duration_ms=1, total_tokens=0, total_cost_usd=0)


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        event_adapter.validate_python(
            {"seq": 1, "ts": TS.isoformat(), "type": "telemetry", "payload": {}}
        )


def test_schema_is_a_tagged_union_for_typescript() -> None:
    """What openapi-typescript sees: a union on `type`, every field present on the wire."""
    schema = event_adapter.json_schema(mode="serialization")

    assert schema["discriminator"]["propertyName"] == "type"
    assert set(schema["discriminator"]["mapping"]) == set(EVENT_TYPES)
    for cls in EVENT_CLASSES:
        assert set(schema["$defs"][cls.__name__]["required"]) == {"seq", "ts", "type", "payload"}
    # Defaults are always serialized, so they're required in the output schema too.
    assert set(schema["$defs"]["StepStarted"]["required"]) == {"step", "round", "tier", "model"}
