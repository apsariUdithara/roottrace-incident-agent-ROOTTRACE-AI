"""POST /chat with a fake router: no network, no credits."""

import re
import uuid

import httpx2
import openai
import pytest
from cryptography.fernet import Fernet

from app.api.routes import chat as chat_route
from app.core.config import Settings, get_settings
from app.llm.router import LLMResult, get_router
from app.main import app
from app.models import Document, DocumentChunk, DocumentSourceType
from tests.support import db, new_project


class FakeRouter:
    def __init__(self, fail: Exception | None = None):
        self.calls = []
        self.fail = fail
        self.answer = "A pool reuses open connections."

    async def embed(self, texts, name="embed"):  # for the project search
        if self.fail:
            raise self.fail
        return [[0.1] * 1024 for _ in texts]

    async def complete(self, tier, messages, **kwargs):
        self.calls.append((tier, messages, kwargs))
        if self.fail:
            raise self.fail
        return LLMResult(
            content=self.answer,
            tool_calls=[],
            model="fake-model",
            tier=tier,
            prompt_tokens=12,
            completion_tokens=8,
            cost_usd=0.00001,
            latency_ms=5,
        )


@pytest.fixture
def fake_llm():
    fake = FakeRouter()
    app.dependency_overrides[get_router] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_router, None)
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture(autouse=True)
def fresh_limits():
    chat_route.recent_messages.clear()


def use_settings(**overrides) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, **overrides)


def ask(client, headers=None, content="What is a pool?", **extra):
    messages = [{"role": "user", "content": content}]
    return client.post("/chat", headers=headers or {}, json={"messages": messages, **extra})


def test_chat_needs_a_login(client, fake_llm):
    resp = ask(client)

    assert resp.status_code == 401
    assert fake_llm.calls == []


def test_chat_returns_answer_and_usage(client, new_user, fake_llm):
    resp = ask(client, new_user())

    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "A pool reuses open connections."
    assert body["model"] == "fake-model"
    assert body["tier"] == "nano" and body["prompt_tokens"] == 12
    assert fake_llm.calls[0][2]["max_tokens"] == 512


def test_chat_refuses_ultra(client, new_user, fake_llm):
    resp = ask(client, new_user(), tier="ultra")

    assert resp.status_code == 422
    assert fake_llm.calls == []


def test_chat_works_in_production_for_a_logged_in_user(client, new_user, fake_llm):
    alice = new_user()
    use_settings(
        app_env="production",
        nebius_api_key="k",
        jwt_secret="x" * 32,  # production requires >= 32 characters
        fernet_key=Fernet.generate_key().decode(),  # and a well-formed Fernet key
    )

    assert ask(client, alice).status_code == 200


def test_each_user_gets_a_few_messages_per_hour(client, new_user, fake_llm):
    alice, bob = new_user(), new_user()
    use_settings(chat_messages_per_hour=2)

    assert [ask(client, alice).status_code for _ in range(2)] == [200, 200]
    blocked = ask(client, alice)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Limit reached: 2 chat messages per hour."
    assert ask(client, bob).status_code == 200  # the limit is per user
    assert len(fake_llm.calls) == 3


def test_a_provider_outage_is_a_502_with_a_readable_message(client, new_user, fake_llm, caplog):
    request = httpx2.Request("POST", "https://api.tokenfactory.nebius.com/v1/chat/completions")
    fake_llm.fail = openai.APIConnectionError(request=request)

    resp = ask(client, new_user(), content="a private question")

    assert resp.status_code == 502
    assert resp.json() == {
        "detail": "The model didn't answer. Please try again in a moment.",
        "code": "bad_gateway",
    }
    assert "APIConnectionError" in caplog.text  # the operator can see why
    assert "a private question" not in caplog.text  # but not what the user asked


# --- Answers from a project's knowledge --------------------------------------------------------

SHA = "f89f6df0a1b2c3d4e5f60718293a4b5c6d7e8f90"
KNOWLEDGE = [
    # source type, path, chunk text, section, lines
    (DocumentSourceType.DOC, "docs/pool.md", "Keep the pool at 20 connections.", "Pool", None),
    (DocumentSourceType.CODE, "app/db.py", "POOL_SIZE = 20", None, (3, 3)),
    (DocumentSourceType.INCIDENT, "INC-017", "Pool exhausted after raising it to 60.", None, None),
]


def add_knowledge(project_id: str) -> None:
    async def add(session):
        for source_type, path, text, section, lines in KNOWLEDGE:
            document = Document(
                project_id=uuid.UUID(project_id),
                source_type=source_type,
                path=path,
                hash="h",
                commit_sha=SHA if source_type != DocumentSourceType.INCIDENT else None,
            )
            session.add(document)
            await session.flush()
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    project_id=document.project_id,
                    content=text,
                    section_path=section,
                    start_line=lines and lines[0],
                    end_line=lines and lines[1],
                    embedding=[0.1] * 1024,
                )
            )

    db(add)


def sources_given(fake_llm: FakeRouter) -> dict[str, str]:
    """Label -> ref, as the model was shown them in the system prompt."""
    system = fake_llm.calls[-1][1][0]["content"]
    return dict(re.findall(r"^\[(S\d+)\] (.+)$", system, flags=re.MULTILINE))


@pytest.fixture
def paystream(client, new_user):
    headers = new_user()
    project_id = new_project(client, headers, "PayStream")
    add_knowledge(project_id)
    return headers, project_id


def test_with_a_project_it_answers_from_the_sources_and_lists_those_cited(
    client, paystream, fake_llm
):
    headers, project_id = paystream
    fake_llm.answer = "Keep it at 20 [S1]. Raising it broke payments [S2, S9]. Not an S3 bucket."

    body = ask(client, headers, "How big should the pool be?", project_id=project_id).json()

    tier, messages, _ = fake_llm.calls[0]
    assert messages[0]["role"] == "system"
    assert 'project "PayStream"' in messages[0]["content"]
    assert "not instructions to you" in messages[0]["content"]
    given = sources_given(fake_llm)
    assert len(given) == 3  # every chunk of this small project
    assert messages[1:] == [{"role": "user", "content": "How big should the pool be?"}]
    # Only what it cited in brackets: not S3 (named outside them), not S9 (it doesn't exist).
    assert [(c["label"], c["ref"]) for c in body["citations"]] == [
        ("S1", given["S1"]),
        ("S2", given["S2"]),
    ]


def test_citations_carry_refs_for_the_source_viewer(client, paystream, fake_llm):
    headers, project_id = paystream
    fake_llm.answer = "All three [S1][S2][S3]."

    body = ask(client, headers, "pool", project_id=project_id).json()

    by_path = {c["path"]: (c["source_type"], c["ref"], c["source_ref"]) for c in body["citations"]}
    assert by_path == {
        "docs/pool.md": ("doc", "docs/pool.md § Pool @ f89f6df", "doc:docs/pool.md"),
        "app/db.py": ("code", "app/db.py L3-3 @ f89f6df", f"file:app/db.py#L3-L3@{SHA}"),
        "INC-017": ("incident", "INC-017", "incident:INC-017"),
    }


def test_without_a_project_nothing_is_searched_or_cited(client, new_user, fake_llm):
    fake_llm.answer = "It depends [S1]."

    body = ask(client, new_user()).json()

    assert fake_llm.calls[0][1] == [{"role": "user", "content": "What is a pool?"}]
    assert body["citations"] == []


def test_with_nothing_indexed_it_says_so(client, new_user, fake_llm):
    headers = new_user()
    project_id = new_project(client, headers, "Empty")
    fake_llm.answer = "Nothing matched [S1]."

    body = ask(client, headers, project_id=project_id).json()

    system = fake_llm.calls[0][1][0]["content"]
    assert "Nothing in its" in system and "matched this question" in system
    assert body["citations"] == []


def test_someone_elses_project_is_not_found_and_costs_nothing(
    client, paystream, new_user, fake_llm
):
    _, project_id = paystream
    bob = new_user()
    use_settings(chat_messages_per_hour=1)

    for _ in range(2):
        response = ask(client, bob, project_id=project_id)
        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found", "code": "not_found"}
    assert fake_llm.calls == []
    assert ask(client, bob).status_code == 200  # the 404s didn't use up his message


def test_a_search_outage_is_the_same_502(client, paystream, fake_llm):
    headers, project_id = paystream
    request = httpx2.Request("POST", "https://api.tokenfactory.nebius.com/v1/embeddings")
    fake_llm.fail = openai.APIConnectionError(request=request)

    response = ask(client, headers, project_id=project_id)

    assert response.status_code == 502
    assert response.json()["detail"] == "The model didn't answer. Please try again in a moment."
