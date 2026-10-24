"""Search the ingested PayStream project. About $0.0002 per search (1 embedding + 1 Nano rerank).

Run from backend/ after scripts/ingest_paystream.py:
    .venv\\Scripts\\python scripts\\search_demo.py "why is the payment API returning 500s?"
    .venv\\Scripts\\python scripts\\search_demo.py "DB_POOL_SIZE" --no-rerank
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.llm.router import get_router  # noqa: E402
from app.models import Project  # noqa: E402
from app.rag.retrieve import search  # noqa: E402


async def main(query: str, k: int, use_reranker: bool) -> None:
    router = get_router()
    async with SessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.name == "PayStream"))
        if project is None:
            sys.exit("PayStream not ingested: run scripts/ingest_paystream.py first")
        hits = await search(session, router, project.id, query, k=k, use_reranker=use_reranker)

    print(f'Search: "{query}"\n')
    for n, hit in enumerate(hits, start=1):
        first_line = hit.content.strip().splitlines()[0][:90]
        print(f"{n}. [{hit.source_type}] {hit.citation}\n   {first_line}\n")
    cost = sum(s["cost_usd"] for s in router.spans)
    print(f"{len(router.spans)} model calls, ${cost:.6f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--no-rerank", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.query, args.k, not args.no_rerank))
