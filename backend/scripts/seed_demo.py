"""Prepare a fresh server for the public demo: the demo user, and PayStream indexed at v1.4.8.

Safe to run again: indexing skips unchanged files, and the password is just reset.

Run from backend/, after `python -m alembic upgrade head`, with DEMO_PASSWORD set
(the "Try demo" login uses it; if unset, a random one is generated and printed once):
    uv run python scripts/seed_demo.py
Needs demo-target/build/paystream (python demo-target/scripts/build_history.py).
"""

import asyncio
import importlib.util
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.llm.router import get_router  # noqa: E402
from app.models import User  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "ingest", Path(__file__).with_name("ingest_paystream.py")
)
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)

DEMO_REF = "v1.4.8"  # the hero scenario: payment API 500s after a pool-size change


async def main() -> None:
    password = os.environ.get("DEMO_PASSWORD")
    generated = password is None
    password = password or secrets.token_urlsafe(12)
    async with SessionLocal() as session:
        project, stats = await ingest.load(session, get_router(), DEMO_REF)
        owner = await session.scalar(select(User).where(User.email == ingest.OWNER_EMAIL))
        owner.password_hash = await hash_password(password)
        await session.commit()
    repo = stats["repo"]
    print(f"Demo ready: project {project.name} ({project.id}) at {DEMO_REF}")
    print(f"  files {repo['indexed']} indexed, {repo['unchanged']} unchanged; logs {stats['logs']}")
    print(f"  login: {ingest.OWNER_EMAIL}")
    if generated:
        print(f"  password (generated, shown once; set DEMO_PASSWORD to choose one): {password}")


asyncio.run(main())
