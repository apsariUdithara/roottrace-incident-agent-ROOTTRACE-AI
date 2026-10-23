"""Follow-up questions about a finished investigation ("Which commit introduced this?").

Short-term memory without a checkpoint store: everything the investigation found is already in
the database (evidence with its labels, the report, the patch), so each question loads that,
adds the earlier chat, and asks Nemotron Super. It may call the read-only tools again for
something new, but answers cite evidence labels like the report does, so they stay checkable.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.events import EventBus
from app.llm.router import LLMRouter
from app.models import (
    Investigation,
    InvestigationEvent,
    Message,
    MessageRole,
    Patch,
    Repository,
)
from app.schemas.events import MessageCompleted
from app.tools import ToolContext, call_tool, openai_schemas

TOOL_BUDGET = 4
LABEL = re.compile(r"\b([FI]\d+)\b")

SYSTEM = """You are RootTrace, answering a follow-up question about an incident investigation
that has already finished. Answer briefly and directly, in Markdown.
- Base your answer on the evidence below and cite labels in square brackets, e.g. [F2].
- If the evidence does not answer the question, you may use the read-only tools. Say clearly
  what you newly checked, and cite the tool's source (file, commit, log) for it.
- If you still cannot tell, say so. Never invent evidence."""

CONTEXT = """Original question: {question}
Status: {status}
Root cause: {root_cause}
Confidence: {confidence}
Report:
{summary}

Evidence (label: statement [source]):
{evidence}

Fix: {fix}"""


async def evidence_lines(session: AsyncSession, investigation_id: uuid.UUID) -> dict[str, dict]:
    """Evidence by label (F1, I1, ...), read back from the events the run emitted."""
    rows = await session.scalars(
        select(InvestigationEvent.payload)
        .where(
            InvestigationEvent.investigation_id == investigation_id,
            InvestigationEvent.type == "evidence_added",
        )
        .order_by(InvestigationEvent.seq)
    )
    return {p["label"]: p for p in rows}


async def answer(
    session: AsyncSession, router: LLMRouter, investigation_id: uuid.UUID, question: str
) -> Message:
    inv = await session.get(Investigation, investigation_id)
    if inv is None or inv.report is None:
        raise ValueError("this investigation has no report yet")
    evidence = await evidence_lines(session, inv.id)
    patch = await session.scalar(
        select(Patch).where(Patch.investigation_id == inv.id).order_by(Patch.attempt.desc())
    )
    fix = "none"
    if patch:
        fix = (
            f"{patch.status}: {patch.explanation or ''} PR: {patch.pr_url or patch.pr_title or '-'}"
        )
    context = CONTEXT.format(
        question=inv.question,
        status=inv.status,
        root_cause=inv.root_cause or "not established",
        confidence=f"{inv.confidence:.0%}" if inv.confidence is not None else "n/a",
        summary=inv.report.get("summary", ""),
        evidence="\n".join(
            f"{label}: {e['statement']} [{e.get('source_ref') or 'inference'}]"
            for label, e in evidence.items()
        )
        or "(none)",
        fix=fix,
    )
    history = await session.scalars(
        select(Message).where(Message.investigation_id == inv.id).order_by(Message.created_at)
    )
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": context},
        {"role": "assistant", "content": "Understood. What would you like to know?"},
        *({"role": m.role.value, "content": m.content} for m in history),
        {"role": "user", "content": question},
    ]

    repo = await session.scalar(select(Repository).where(Repository.project_id == inv.project_id))
    tools = ToolContext(
        session,
        router,
        inv.project_id,
        Path(repo.local_path),
        ref=inv.report.get("deployed_ref", "HEAD"),
    )
    reply = ""
    for _ in range(TOOL_BUDGET + 1):
        last = len(tools.trace) >= TOOL_BUDGET
        result = await router.complete(
            "super",
            messages,
            tools=None if last else openai_schemas(),
            max_tokens=2000,
            name="followup",
        )
        if not result.tool_calls:
            reply = result.content or ""
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
            output = await call_tool(tools, call["name"], call["arguments"])
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})

    reply = reply.strip() or "I couldn't find an answer in the evidence."
    # Own timestamps: the database clock gives both the same time within one transaction.
    now = datetime.now(UTC)
    session.add(
        Message(
            investigation_id=inv.id,
            role=MessageRole.USER,
            content=question,
            created_at=now,
        )
    )
    message = Message(
        investigation_id=inv.id,
        role=MessageRole.ASSISTANT,
        content=reply,
        created_at=now + timedelta(microseconds=1),
    )
    session.add(message)
    await session.flush()
    cited = [
        uuid.UUID(evidence[label]["evidence_id"])
        for label in dict.fromkeys(LABEL.findall(reply))
        if label in evidence
    ]
    bus = await EventBus.resume(session, inv.id)
    await bus.publish(MessageCompleted(message_id=message.id, content=reply, evidence_ids=cited))
    return message
