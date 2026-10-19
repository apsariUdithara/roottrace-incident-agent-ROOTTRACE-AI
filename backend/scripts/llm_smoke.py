"""Live check of the model router against Nebius. Makes 2 small Nano calls (well under 1 cent).

Run from backend/: uv run python scripts/llm_smoke.py
"""

import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from app.core.config import Settings  # noqa: E402
from app.llm.router import LLMRouter  # noqa: E402


class RootCause(BaseModel):
    root_cause: str
    confidence: float


async def main() -> None:
    # app_env="test" switches the cache off: we want real calls.
    router = LLMRouter(Settings(app_env="test"))

    plain = await router.complete(
        "nano",
        [{"role": "user", "content": "In one sentence: what does a database connection pool do?"}],
        max_tokens=300,
        name="smoke-plain",
    )
    print("1. Plain answer:\n  ", plain.content, "\n")

    structured = await router.complete(
        "nano",
        [
            {
                "role": "user",
                "content": "Logs show 'too many connections' right after DB_POOL_SIZE went "
                "from 20 to 100 on 5 instances, with max_connections = 100. "
                "Give the root cause and a confidence from 0 to 1.",
            }
        ],
        schema=RootCause,
        max_tokens=600,
        name="smoke-json",
    )
    print("2. Parsed JSON:\n  ", structured.parsed, "\n")

    print("Spans:")
    for s in router.spans:
        print(
            f"   {s['name']:<12} {s['model']:<40} "
            f"{s['prompt_tokens']:>5} in {s['completion_tokens']:>5} out"
            f"  ${s['cost_usd']:.6f}  {s['latency_ms']} ms"
        )
    print(f"Total cost: ${sum(s['cost_usd'] for s in router.spans):.6f}")


asyncio.run(main())
