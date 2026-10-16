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
)
MAX_ATTEMPTS = 3


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

    def __post_init__(self) -> None:
        if self.client is None:
            key = self.settings.nebius_api_key
            self.client = openai.AsyncOpenAI(
                base_url=self.settings.nebius_base_url,
                api_key=key.get_secret_value() if key else None,
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
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await self._call(tier, request)
            except RETRYABLE:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                await self.sleep(2**attempt)  # wait 1 s, then 2 s
        raise AssertionError("unreachable")

    async def _call(self, tier: Tier, request: dict) -> LLMResult:
        model = self.model_for(tier)
        start = time.perf_counter()
        resp = await self.client.chat.completions.create(model=model, **request)
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
