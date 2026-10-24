"""POST /chat with a fake router: no network, no credits."""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.llm.router import LLMResult, get_router
from app.main import app


class FakeRouter:
    def __init__(self):
        self.calls = []

    async def complete(self, tier, messages, **kwargs):
        self.calls.append((tier, messages, kwargs))
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
def client():
    fake = FakeRouter()
    app.dependency_overrides[get_router] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, app_env="development")
    yield TestClient(app), fake
    app.dependency_overrides.clear()


def test_chat_returns_answer_and_usage(client):
    http, fake = client
    resp = http.post("/chat", json={"messages": [{"role": "user", "content": "What is a pool?"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "A pool reuses open connections."
    assert body["tier"] == "nano" and body["prompt_tokens"] == 12
    assert fake.calls[0][2]["max_tokens"] == 512


def test_chat_refuses_ultra(client):
    http, fake = client
    resp = http.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}], "tier": "ultra"}
    )
    assert resp.status_code == 422
    assert fake.calls == []


def test_chat_is_disabled_in_production(client):
    http, fake = client
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        app_env="production",
        nebius_api_key="k",
        jwt_secret="x" * 32,  # production requires >= 32 characters
        fernet_key=Fernet.generate_key().decode(),  # and a well-formed Fernet key
    )
    resp = http.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 403
    assert fake.calls == []
