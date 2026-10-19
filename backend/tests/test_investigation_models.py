from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Evidence,
    EvidenceKind,
    EvidenceSourceType,
    Hypothesis,
    Investigation,
    InvestigationEvent,
    InvestigationStatus,
    Memory,
    MemoryAuthor,
    MemoryCategory,
    Message,
    MessageRole,
    ModelTier,
    Patch,
    Project,
    SpanKind,
    SpanStatus,
    Stance,
    TestPhase,
    TestRun,
    TraceSpan,
)


async def add_investigation(session: AsyncSession, project: Project) -> Investigation:
    investigation = Investigation(
        project_id=project.id, user_id=project.owner_id, question="Why are payments failing?"
    )
    session.add(investigation)
    await session.flush()
    return investigation


def cited_fact(investigation: Investigation) -> Evidence:
    return Evidence(
        investigation_id=investigation.id,
        kind=EvidenceKind.FACT,
        statement="23 OperationalError: too many connections",
        tool="search_logs",
        source_type=EvidenceSourceType.LOG,
        source_ref="log:4bf92f35",
        excerpt="OperationalError: too many connections",
    )


async def test_investigation_defaults(db_session: AsyncSession, project: Project) -> None:
    investigation = await add_investigation(db_session, project)
    await db_session.refresh(investigation)

    assert investigation.status is InvestigationStatus.QUEUED
    assert investigation.started_at is not None
    assert (investigation.total_tokens, investigation.total_cost_usd) == (0, Decimal(0))


async def test_facts_must_be_cited(db_session: AsyncSession, project: Project) -> None:
    investigation = await add_investigation(db_session, project)
    db_session.add(
        Evidence(
            investigation_id=investigation.id,
            kind=EvidenceKind.FACT,
            statement="Pool size was raised",
            tool="search_github_commits",
        )
    )

    with pytest.raises(IntegrityError, match="ck_evidence_fact_is_cited"):
        await db_session.flush()


async def test_inferences_cite_facts_instead_of_a_source(
    db_session: AsyncSession, project: Project
) -> None:
    investigation = await add_investigation(db_session, project)
    fact = cited_fact(investigation)
    db_session.add(fact)
    await db_session.flush()

    inference = Evidence(
        investigation_id=investigation.id,
        kind=EvidenceKind.INFERENCE,
        statement="Pool exhaustion is causing the 500s",
        cites=[fact.id],
    )
    db_session.add(inference)
    await db_session.flush()
    await db_session.refresh(inference)

    assert inference.cites == [fact.id]
    assert inference.stance is Stance.NEUTRAL


async def test_event_sequence_is_unique_per_investigation(
    db_session: AsyncSession, project: Project
) -> None:
    investigation = await add_investigation(db_session, project)
    db_session.add_all(
        [
            InvestigationEvent(investigation_id=investigation.id, seq=1, type="step_started"),
            InvestigationEvent(investigation_id=investigation.id, seq=1, type="step_completed"),
        ]
    )

    with pytest.raises(IntegrityError, match="uq_investigation_events_investigation_id_seq"):
        await db_session.flush()


async def test_trace_span_defaults(db_session: AsyncSession, project: Project) -> None:
    span = TraceSpan(
        investigation_id=(await add_investigation(db_session, project)).id,
        kind=SpanKind.LLM,
        name="plan",
        model="nvidia/nemotron-3-super-120b-a12b",
        tier=ModelTier.SUPER,
    )
    db_session.add(span)
    await db_session.flush()
    await db_session.refresh(span)

    assert (span.status, span.cached, span.downgraded) == (SpanStatus.OK, False, False)
    assert span.cost_usd == Decimal(0)
    assert span.started_at is not None


async def test_deleting_an_investigation_removes_its_records(
    db_session: AsyncSession, project: Project
) -> None:
    investigation = await add_investigation(db_session, project)
    hypothesis = Hypothesis(investigation_id=investigation.id, statement="Pool exhaustion")
    patch = Patch(investigation_id=investigation.id, attempt=1, diff="--- a\n+++ b\n")
    db_session.add_all([hypothesis, patch])
    await db_session.flush()
    fact = cited_fact(investigation)
    fact.supports_hypothesis_id = hypothesis.id
    memory = Memory(
        project_id=project.id,
        category=MemoryCategory.PROJECT_FACT,
        content="The payment service uses PostgreSQL",
        created_by=MemoryAuthor.AGENT,
        source_investigation_id=investigation.id,
    )
    db_session.add_all(
        [
            fact,
            memory,
            InvestigationEvent(investigation_id=investigation.id, seq=1, type="done"),
            Message(investigation_id=investigation.id, role=MessageRole.USER, content="Why?"),
            TestRun(patch_id=patch.id, phase=TestPhase.BASELINE, command="pytest -q"),
            TraceSpan(investigation_id=investigation.id, kind=SpanKind.TOOL, name="search_logs"),
        ]
    )
    await db_session.flush()

    await db_session.delete(investigation)
    await db_session.flush()

    async def remaining(model: type, column: object, value: object) -> int:
        return await db_session.scalar(
            select(func.count()).select_from(model).where(column == value)
        )

    for model in (InvestigationEvent, Hypothesis, Evidence, Patch, Message, TraceSpan):
        assert await remaining(model, model.investigation_id, investigation.id) == 0, model
    assert await remaining(TestRun, TestRun.patch_id, patch.id) == 0
    # Memories outlive the investigation that produced them.
    assert (
        await db_session.scalar(
            select(Memory.source_investigation_id).where(Memory.id == memory.id)
        )
        is None
    )
