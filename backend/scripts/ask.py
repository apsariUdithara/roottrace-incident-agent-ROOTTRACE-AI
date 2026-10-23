"""Ask a follow-up question about the latest PayStream investigation.

Run from backend/:
    uv run python scripts/ask.py "Which commit introduced this?"
    uv run python scripts/ask.py "Was this seen before?"
Each question is one or a few Nemotron Super calls (well under 1 cent).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from sqlalchemy import select  # noqa: E402

from app.agent.followup import answer  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.llm.router import get_router  # noqa: E402
from app.models import Investigation, Memory, Project  # noqa: E402


async def main(question: str) -> None:
    router = get_router()
    async with SessionLocal() as session:
        inv = await session.scalar(
            select(Investigation)
            .join(Project, Investigation.project_id == Project.id)
            .where(Project.name == "PayStream", Investigation.report.is_not(None))
            .order_by(Investigation.started_at.desc())
            .limit(1)
        )
        if inv is None:
            sys.exit("No finished investigation yet: run scripts/investigate.py first.")
        message = await answer(session, router, inv.id, question)
        await session.commit()
        print(f"Q: {question}\n\nA: {message.content}")
        memories = (
            await session.scalars(select(Memory).where(Memory.project_id == inv.project_id))
        ).all()
        if memories:
            print("\nLong-term memories for this project:")
            for m in memories:
                print(f"  - ({m.category}, by {m.created_by}) {m.content}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: python scripts/ask.py "your question"')
    asyncio.run(main(sys.argv[1]))
