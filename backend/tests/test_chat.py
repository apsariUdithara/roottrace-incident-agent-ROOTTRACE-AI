"""POST /chat with a fake router: no network, no credits."""

import httpx2
import openai
import pytest
from cryptography.fernet import Fernet

from app.api.routes import chat as chat_route
from app.core.config import Settings, get_settings
from app.llm.router import LLMResult, get_router
from app.main import app


class FakeRouter:
    def __init__(self, fail: Exception | None = None):
        self.calls = []
        self.fail = fail

    async def complete(self, tier, messages, **kwargs):
        self.calls.append((tier, messages, kwargs))
        if self.fail:
            raise self.fail
        return LLMResult(
            content="A pool reuses open connections.",
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
