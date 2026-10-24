"""Router tests with a fake Nebius client: no network, no credits."""

import json
from types import SimpleNamespace

import httpx2
import openai
import pytest
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.llm.router import MAX_ATTEMPTS, LLMRouter

pytestmark = pytest.mark.anyio  # run the async tests (anyio's pytest plugin is already installed)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class RootCause(BaseModel):
    root_cause: str
    confidence: float


def reply(content=None, tool_calls=(), prompt_tokens=10, completion_tokens=5):
    calls = [
        SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)))
        for i, (n, a) in enumerate(tool_calls)
    ]
    message = SimpleNamespace(content=content, tool_calls=calls or None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def connection_error():
    return openai.APIConnectionError(request=httpx2.Request("POST", "https://example.invalid"))


class FakeNebius:
    """Returns (or raises) the given replies in order and records every request."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.requests.append(request)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


async def no_sleep(_):
    pass


def make_router(fake, env="development", cache_dir=None):
    return LLMRouter(
        Settings(_env_file=None, app_env=env), client=fake, cache_dir=cache_dir, sleep=no_sleep
    )


async def test_tier_is_mapped_to_its_model():
    fake = FakeNebius(reply("hi"))
    router = make_router(fake)
    await router.complete("nano", [{"role": "user", "content": "hi"}])
    assert fake.requests[0]["model"] == router.settings.nemotron_nano


async def test_cost_uses_input_and_output_prices():
    # 1M tokens in and 1M out on Super: $0.30 + $0.90.
    fake = FakeNebius(reply("hi", prompt_tokens=1_000_000, completion_tokens=1_000_000))
    result = await make_router(fake).complete("super", [{"role": "user", "content": "hi"}])
    assert result.cost_usd == pytest.approx(1.20)


async def test_tool_calls_are_returned():
    fake = FakeNebius(reply(tool_calls=[("search_logs", {"service": "payments-api"})]))
    result = await make_router(fake).complete("super", [{"role": "user", "content": "find errors"}])
    assert result.tool_calls[0]["name"] == "search_logs"


async def test_valid_json_is_parsed():
    fake = FakeNebius(reply('{"root_cause": "pool too big", "confidence": 0.8}'))
    result = await make_router(fake).complete(
        "super", [{"role": "user", "content": "why?"}], schema=RootCause
    )
    assert result.parsed == RootCause(root_cause="pool too big", confidence=0.8)


async def test_invalid_json_is_retried_once_with_the_error():
    fake = FakeNebius(
        reply("Here's my thinking..."), reply('{"root_cause": "pool", "confidence": 0.7}')
    )
    result = await make_router(fake).complete(
        "super", [{"role": "user", "content": "why?"}], schema=RootCause
    )
    assert result.parsed.root_cause == "pool"
    assert "not valid" in fake.requests[1]["messages"][-1]["content"]


async def test_second_identical_call_comes_from_cache(tmp_path):
    fake = FakeNebius(reply("hi"))
    router = make_router(fake, cache_dir=tmp_path)
    await router.complete("nano", [{"role": "user", "content": "hi"}])
    again = await router.complete("nano", [{"role": "user", "content": "hi"}])
    assert again.cached and again.cost_usd == 0
    assert len(fake.requests) == 1


async def test_invalid_answers_are_never_cached(tmp_path):
    fake = FakeNebius(reply("not json"), reply("still not json"))
    router = make_router(fake, cache_dir=tmp_path)
    with pytest.raises(ValidationError):
        await router.complete("super", [{"role": "user", "content": "why?"}], schema=RootCause)
    assert list(tmp_path.iterdir()) == []


async def test_cache_is_off_outside_development(tmp_path):
    fake = FakeNebius(reply("hi"), reply("hi"))
    router = make_router(fake, env="test", cache_dir=tmp_path)
    await router.complete("nano", [{"role": "user", "content": "hi"}])
    await router.complete("nano", [{"role": "user", "content": "hi"}])
    assert len(fake.requests) == 2


async def test_ultra_falls_back_to_super_when_unavailable():
    fake = FakeNebius(*[connection_error() for _ in range(MAX_ATTEMPTS)], reply("ok"))
    router = make_router(fake)
    result = await router.complete("ultra", [{"role": "user", "content": "hi"}])
    assert result.downgraded
    assert fake.requests[-1]["model"] == router.settings.nemotron_super


async def test_every_call_records_a_span():
    fake = FakeNebius(reply("hi"))
    router = make_router(fake)
    await router.complete("nano", [{"role": "user", "content": "hi"}], name="triage")
    span = router.spans[0]
    assert span["name"] == "triage" and span["model"] == router.settings.nemotron_nano


def test_real_client_has_a_timeout_and_leaves_retries_to_the_router():
    router = LLMRouter(Settings(_env_file=None, nebius_api_key="k"))
    assert router.client.timeout == 180 and router.client.max_retries == 0


async def test_a_timed_out_call_is_retried():
    timeout = openai.APITimeoutError(request=httpx2.Request("POST", "https://example.invalid"))
    fake = FakeNebius(timeout, reply("hi"))
    result = await make_router(fake).complete("nano", [{"role": "user", "content": "hi"}])
    assert result.content == "hi" and len(fake.requests) == 2


async def test_a_call_that_never_finishes_hits_the_deadline_and_is_retried(monkeypatch):
    import asyncio

    import app.llm.router as router_module

    monkeypatch.setattr(router_module, "CALL_DEADLINE_S", 0.05)

    class Stalling(FakeNebius):
        async def create(self, **request):
            self.requests.append(request)
            if len(self.requests) == 1:
                await asyncio.sleep(10)  # a server that never finishes
            return reply("hi")

    fake = Stalling()
    result = await make_router(fake).complete("nano", [{"role": "user", "content": "hi"}])
    assert result.content == "hi" and len(fake.requests) == 2


async def test_retries_wait_longer_each_time():
    waits = []

    async def record(seconds):
        waits.append(seconds)

    fake = FakeNebius(connection_error(), connection_error(), reply("ok"))
    router = LLMRouter(Settings(_env_file=None), client=fake, sleep=record)
    await router.complete("nano", [{"role": "user", "content": "hi"}])
    assert waits == [2, 10]


async def test_a_run_stops_once_it_reaches_its_cost_cap():
    from app.llm.router import CostCapExceeded

    # Each Super call of 1M tokens in and out costs $1.20.
    big = reply("hi", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    fake = FakeNebius(big, big)
    router = make_router(fake)
    router.cost_cap_usd = 1.00
    await router.complete("super", [{"role": "user", "content": "hi"}])  # spends $1.20
    with pytest.raises(CostCapExceeded, match=r"\$1\.00"):
        await router.complete("super", [{"role": "user", "content": "again"}])
    assert len(fake.requests) == 1  # the second call never reached Nebius
