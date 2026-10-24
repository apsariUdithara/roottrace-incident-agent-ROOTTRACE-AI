"""Follow-up questions on a finished investigation, with a fake model (no credits)."""

import json

from sqlalchemy import select

from app.agent.followup import answer
from app.llm.router import LLMResult
from app.models import InvestigationEvent, Message, Repository
from tests.test_agent import SUPPORTED, ScriptedModel, investigate
from tests.test_tools import repo  # noqa: F401 - pytest fixture


def reply(content="", tool_calls=()):
    return LLMResult(
        content=content,
        tool_calls=list(tool_calls),
        model="fake",
        tier="super",
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.0,
        latency_ms=1,
    )


class FollowupModel:
    """Answers from a script and records every request it receives."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    async def complete(self, tier, messages, **kwargs):
        self.requests.append(messages)
        return self.replies.pop(0)


async def finished(db_session, project, repo):  # noqa: F811
    db_session.add(
        Repository(project_id=project.id, url="https://github.com/acme/demo", local_path=str(repo))
    )
    inv, _, _ = await investigate(db_session, project, repo, ScriptedModel([SUPPORTED]))
    return inv


async def test_a_follow_up_is_answered_from_the_evidence_and_saved(db_session, project, repo):  # noqa: F811
    inv = await finished(db_session, project, repo)
    model = FollowupModel(reply("The pool was raised to 100 in that commit [F2]."))
    message = await answer(db_session, model, inv.id, "Which commit introduced this?")

    context = model.requests[0][1]["content"]
    assert "F1: Pool size is 100" in context and "Root cause: Pool size raised to 100" in context
    assert message.content.endswith("[F2].")
    chat = (
        await db_session.scalars(
            select(Message.role)
            .where(Message.investigation_id == inv.id)
            .order_by(Message.created_at)
        )
    ).all()
    assert chat == ["user", "assistant"]
    event = await db_session.scalar(
        select(InvestigationEvent)
        .where(InvestigationEvent.investigation_id == inv.id)
        .order_by(InvestigationEvent.seq.desc())
    )
    assert event.type == "message" and len(event.payload["evidence_ids"]) == 1  # F2 resolved


async def test_the_second_question_sees_the_first_answer(db_session, project, repo):  # noqa: F811
    inv = await finished(db_session, project, repo)
    model = FollowupModel(reply("Commit A [F2]."), reply("Yes, INC-017 [F1]."))
    await answer(db_session, model, inv.id, "Which commit?")
    await answer(db_session, model, inv.id, "Was this seen before?")
    history = [(m["role"], m["content"]) for m in model.requests[1][3:]]
    assert history == [
        ("user", "Which commit?"),
        ("assistant", "Commit A [F2]."),
        ("user", "Was this seen before?"),
    ]


async def test_a_follow_up_can_check_something_new_with_a_tool(db_session, project, repo):  # noqa: F811
    inv = await finished(db_session, project, repo)
    call = {"id": "c1", "name": "get_file", "arguments": json.dumps({"path": "config.py"})}
    model = FollowupModel(reply(tool_calls=[call]), reply("Newly checked: the file says 100."))
    message = await answer(db_session, model, inv.id, "What does the config say now?")
    tool_result = model.requests[1][-1]
    assert tool_result["role"] == "tool" and "DB_POOL_SIZE = 100" in tool_result["content"]
    assert message.content.startswith("Newly checked")
