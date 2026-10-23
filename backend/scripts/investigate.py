"""Run one investigation on PayStream from the terminal, printing the agent's events live.

Run from backend/, after scripts/ingest_paystream.py:
    uv run python scripts/investigate.py
    uv run python scripts/investigate.py "Customers report being charged twice" --ref v1.4.3

Cost: roughly 20-40 model calls, mostly Super; about $0.05-0.15 with Ultra pointed at Super.
In development, answers are cached: re-running the same investigation costs nothing.
"""

import argparse
import asyncio
import sys
import textwrap
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from sqlalchemy import select  # noqa: E402

from app.agent.run import run_investigation  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.llm.router import get_router  # noqa: E402
from app.models import Project  # noqa: E402

HERO = "The payment API started returning 500 errors after today's deployment. Find out why."


def show(event) -> None:
    p = event.payload
    t = event.type
    if t == "step_started":
        print(
            f"\n▶ {p.step}"
            + (f" (round {p.round})" if p.round > 1 else "")
            + (f"  [{p.tier}]" if p.tier else "")
        )
    elif t == "step_completed" and p.summary:
        print(f"  ✓ {p.summary}  ({p.duration_ms / 1000:.1f}s)")
    elif t == "tool_call":
        print(f"  → {p.tool}({', '.join(f'{k}={v!r}' for k, v in p.arguments.items())})")
    elif t == "tool_result":
        print(f"    {'·' if p.ok else '✗'} {p.summary}")
    elif t == "evidence_added":
        cites = f"  ← {len(p.cites)} facts" if p.cites else ""
        print(
            f"  [{p.label}] {p.statement}"
            + (f"\n        {p.source_ref}" if p.source_ref else cites)
        )
    elif t == "hypothesis":
        print(f"  H{p.rank}: {p.statement}")
    elif t == "verdict":
        print(f"  VERDICT: {p.verdict.upper()} — {p.notes}")
        for gap in p.gaps:
            print(f"    gap: {gap}")
    elif t == "confidence":
        parts = ", ".join(f"{c.name} {c.value:.2f}" for c in p.components)
        print(f"  CONFIDENCE {p.score:.0%}  ({parts})")
    elif t == "report":
        print("\n" + "=" * 70 + f"\nROOT CAUSE: {p.root_cause}\n")
        print(textwrap.indent(p.summary, "  "))
        print("\nNext steps:\n" + "\n".join(f"  - {s}" for s in p.next_steps))
    elif t == "error":
        print(f"\n✗ ERROR: {p.message}")
    elif t == "done":
        took = f"{p.duration_ms / 1000:.0f}s"
        print(f"\n{p.status} in {took} · {p.total_tokens} tokens · ${p.total_cost_usd:.4f}")


async def main(question: str, ref: str, reported_at: str) -> None:
    router = get_router()
    async with SessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.name == "PayStream"))
        if project is None:
            sys.exit("Run scripts/ingest_paystream.py first.")
        print(f"Investigating PayStream at {ref}: {question}")
        await run_investigation(
            session,
            router,
            project.id,
            project.owner_id,
            question,
            ref=ref,
            reported_at=datetime.fromisoformat(reported_at),
            listeners=[show],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default=HERO)
    parser.add_argument("--ref", default="v1.4.8")
    parser.add_argument(
        "--reported-at",
        default="2026-10-06T15:10:00+00:00",
        help="When the on-call engineer opened the investigation",
    )
    args = parser.parse_args()
    asyncio.run(main(args.question, args.ref, args.reported_at))
