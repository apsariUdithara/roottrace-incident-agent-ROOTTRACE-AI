"""Walk through the hero incident with the agent's tools, by hand, on real PayStream data.

This is the investigation an engineer would do. In Phase 5 the agent chooses these calls itself.
Costs: 2 searches with Nano reranking, about $0.001. Everything else is free (git, SQL).

Run from backend/, after scripts/ingest_paystream.py:
    uv run python scripts/tools_demo.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.llm.router import get_router  # noqa: E402
from app.models import Project, Repository  # noqa: E402
from app.tools import ToolContext, call_tool  # noqa: E402

STEPS = [
    (
        "What do the logs say around the deploy?",
        "search_logs",
        {"since": "2026-10-06T14:00:00Z", "until": "2026-10-06T15:30:00Z", "route": "/payments"},
    ),
    ("What changed recently?", "search_github_commits", {"since": "2026-10-05"}),
    ("What exactly did the suspicious commit change?", "get_commit_diff", {"sha": "HEAD~1"}),
    (
        "What do the docs say about the pool?",
        "search_documents",
        {"query": "connection pool size max_connections", "k": 3},
    ),
    (
        "Has this happened before?",
        "search_previous_incidents",
        {"query": "too many connections after raising pool size", "k": 3},
    ),
    (
        "How bad is it in production data?",
        "query_database",
        {
            "sql": "SELECT http_status, count(*) AS n FROM payments "
            "WHERE created_at >= '2026-10-06' GROUP BY 1 ORDER BY 1"
        },
    ),
    ("Guard check: try to read a file outside the repo", "get_file", {"path": "../../.env"}),
]


async def main() -> None:
    router = get_router()
    async with SessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.name == "PayStream"))
        repo = await session.scalar(select(Repository).where(Repository.project_id == project.id))
        if repo is None or not repo.local_path:
            sys.exit("Run scripts/ingest_paystream.py first (it records the repository path).")
        ctx = ToolContext(session, router, project.id, Path(repo.local_path), ref="v1.4.8")

        for question, tool, args in STEPS:
            if tool == "get_commit_diff":  # resolve HEAD~1 of the release to a real SHA
                commits = json.loads(await call_tool(ctx, "search_github_commits", {}))
                args = {"sha": commits["commits"][1]["sha"]}
            print(f"\n=== {question}\n>>> {tool}({json.dumps(args)})")
            print(json.dumps(json.loads(await call_tool(ctx, tool, args)), indent=2)[:2500])

    print(
        f"\n{len(ctx.trace)} tool calls, {len(router.spans)} model calls, "
        f"${sum(s['cost_usd'] for s in router.spans):.6f}"
    )


asyncio.run(main())
