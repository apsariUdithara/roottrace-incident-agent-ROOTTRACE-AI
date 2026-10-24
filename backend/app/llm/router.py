"""One doorway to the Nemotron models on Nebius Token Factory.

Every LLM call in RootTrace goes through LLMRouter.complete(), which:
  1. maps a tier (ultra / super / nano) to a model ID from settings,
  2. retries network and overload errors, then falls back from Ultra to Super,
  3. when given a Pydantic schema, asks for JSON in that shape, validates it,
     and retries once with the validation error,
  4. records a span for every call: model, tokens, latency, cost,
  5. in development only, caches validated answers on disk.
"""

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import openai
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.llm.pricing import cost_usd

Tier = Literal["ultra", "super", "nano"]

# Errors worth retrying: the request may succeed if sent again.
RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
    TimeoutError,  # our own deadline (CALL_DEADLINE_S) was reached
)
# Waits between attempts: long enough to ride out a short network outage, not just one
# dropped request (a 1-2 s wait failed a whole investigation during an outage).
BACKOFF_S = (2, 10, 30)
MAX_ATTEMPTS = len(BACKOFF_S) + 1


class CostCapExceeded(Exception):
    """A run reached its spending limit. Not retryable: the run should stop."""


# Longest a single model call may take. Without a limit, a stalled connection (e.g. after the
# machine slept) waits forever; with one, it raises APITimeoutError and our retry takes over.
CALL_TIMEOUT_S = 180
CALL_DEADLINE_S = 240  # total time for one call, however the server behaves
# ponytail: embeddings are not cached; ingestion already skips unchanged files by content hash.
EMBED_BATCH = 64


@dataclass
class LLMResult:
    content: str | None
    tool_calls: list[dict]
    model: str
    tier: Tier
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    parsed: Any = None
    cached: bool = False
    downgraded: bool = False


@dataclass
class LLMRouter:
    settings: Settings
    client: Any = None
    cache_dir: Path | None = None
    sleep: Any = asyncio.sleep
    # ponytail: spans kept in memory for now; they move to the trace_spans table in Phase 5.
    spans: list[dict] = field(default_factory=list)
    # Spending limit for everything this router does (one investigation). None: no limit.
    cost_cap_usd: float | None = None

    def spent_usd(self) -> float:
        return sum(s["cost_usd"] for s in self.spans)

    def check_budget(self) -> None:
        """Refuse the next call once this router's spending reached its cap."""
        if self.cost_cap_usd is not None and self.spent_usd() >= self.cost_cap_usd:
            raise CostCapExceeded(
                f"stopped: this investigation reached its cost limit of ${self.cost_cap_usd:.2f}"
            )

    def __post_init__(self) -> None:
        if self.client is None:
            key = self.settings.nebius_api_key
            self.client = openai.AsyncOpenAI(
                base_url=self.settings.nebius_base_url,
                api_key=key.get_secret_value() if key else None,
                timeout=CALL_TIMEOUT_S,
                max_retries=0,  # retries are ours (_with_retries), so they show in traces
            )
        if self.settings.app_env != "development":
            self.cache_dir = None  # never serve saved answers in evaluation or production

    def model_for(self, tier: Tier) -> str:
        s = self.settings
        return {"ultra": s.nemotron_ultra, "super": s.nemotron_super, "nano": s.nemotron_nano}[tier]

    async def complete(
        self,
        tier: Tier,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        schema: type[BaseModel] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        name: str = "llm",
    ) -> LLMResult:
        self.check_budget()
        request: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            request["tools"] = tools
        if schema:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }

        result = await self._with_fallback(tier, request)
        if schema:
            result = await self._validate(tier, request, schema, result)

        self._save_to_cache(tier, request, result)
        self.spans.append(
            {
                "name": name,
                **{
                    k: v
                    for k, v in asdict(result).items()
                    if k not in ("content", "tool_calls", "parsed")
                },
            }
        )
        return result

    async def _with_fallback(self, tier: Tier, request: dict) -> LLMResult:
        try:
            return await self._with_retries(tier, request)
        except RETRYABLE:
            if tier != "ultra":
                raise
            result = await self._with_retries("super", request)
            result.downgraded = True
            return result

    async def _with_retries(self, tier: Tier, request: dict) -> LLMResult:
        if cached := self._load_from_cache(tier, request):
            return cached
        return await self._retry(lambda: self._call(tier, request))

    async def _retry(self, make_call):
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await make_call()
            except RETRYABLE:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                await self.sleep(BACKOFF_S[attempt])
        raise AssertionError("unreachable")

    async def embed(self, texts: list[str], name: str = "embed") -> list[list[float]]:
        """Embed texts in batches with the embedding model; one span per batch."""
        model, vectors = self.settings.embed_model, []
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i : i + EMBED_BATCH]
            self.check_budget()
            start = time.perf_counter()
            resp = await self._retry(
                lambda batch=batch: self.client.embeddings.create(
                    model=model, input=batch, dimensions=self.settings.embed_dimensions
                )
            )
            vectors += [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            tokens = resp.usage.prompt_tokens
            self.spans.append(
                {
                    "name": name,
                    "model": model,
                    "tier": "embed",
                    "prompt_tokens": tokens,
                    "completion_tokens": 0,
                    "cost_usd": cost_usd(model, tokens, 0),
                    "latency_ms": round((time.perf_counter() - start) * 1000),
                }
            )
        return vectors

    async def _call(self, tier: Tier, request: dict) -> LLMResult:
        model = self.model_for(tier)
        start = time.perf_counter()
        # A hard limit on the WHOLE call: the client's timeout only limits the wait between
        # chunks, so a server that keeps sending (e.g. a model emitting blank lines) never
        # trips it. TimeoutError is retryable.
        resp = await asyncio.wait_for(
            self.client.chat.completions.create(model=model, **request), CALL_DEADLINE_S
        )
        message = resp.choices[0].message
        usage = resp.usage
        return LLMResult(
            content=message.content,
            tool_calls=[
                {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                for c in (message.tool_calls or [])
            ],
            model=model,
            tier=tier,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=cost_usd(model, usage.prompt_tokens, usage.completion_tokens),
            latency_ms=round((time.perf_counter() - start) * 1000),
        )

    async def _validate(
        self, tier: Tier, request: dict, schema: type[BaseModel], result: LLMResult
    ) -> LLMResult:
        try:
            result.parsed = schema.model_validate_json(result.content or "")
            return result
        except ValidationError as err:
            retry = dict(request)
            retry["messages"] = [
                *request["messages"],
                {"role": "assistant", "content": result.content or ""},
                {
                    "role": "user",
                    "content": f"That answer was not valid: {err}. "
                    "Reply again with only JSON matching the schema.",
                },
            ]
            second = await self._with_fallback(tier, retry)
            second.parsed = schema.model_validate_json(
                second.content or ""
            )  # raises if still invalid
            second.prompt_tokens += result.prompt_tokens
            second.completion_tokens += result.completion_tokens
            second.cost_usd += result.cost_usd
            return second

    # --- dev cache: validated answers only, keyed on everything that can change the answer ---

    def _cache_path(self, tier: Tier, request: dict) -> Path | None:
        if not self.cache_dir:
            return None
        key = json.dumps({"model": self.model_for(tier), **request}, sort_keys=True, default=str)
        return self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def _load_from_cache(self, tier: Tier, request: dict) -> LLMResult | None:
        path = self._cache_path(tier, request)
        if not path or not path.exists():
            return None
        result = LLMResult(**json.loads(path.read_text(encoding="utf-8")))
        result.cached, result.cost_usd, result.latency_ms = True, 0.0, 0
        return result

    def _save_to_cache(self, tier: Tier, request: dict, result: LLMResult) -> None:
        path = self._cache_path(tier, request)
        if not path or result.cached:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(result).items() if k != "parsed"}
        path.write_text(json.dumps(data), encoding="utf-8")


@lru_cache
def get_router() -> LLMRouter:
    """The app's single router. Its dev cache lives in backend/.cache/llm (ignored by git)."""
    return LLMRouter(get_settings(), cache_dir=Path(".cache/llm"))
