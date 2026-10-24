"""Load PayStream into the development database: code, docs, past incidents, issues and logs.

Embeds about 30k tokens with Qwen3-Embedding-8B, roughly $0.0003. Re-runs only embed changes.

Run from backend/, after `python -m alembic upgrade head`:
    uv run python scripts/ingest_paystream.py            # code at the hero release, v1.4.8
    uv run python scripts/ingest_paystream.py --ref v1.4.1
Needs demo-target/build/paystream (run demo-target/scripts/build_history.py first).
"""

import argparse
import asyncio
import json
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.llm.router import get_router  # noqa: E402
from app.models import Project, User  # noqa: E402
from app.rag.ingest import ingest_incidents, ingest_issues, ingest_logs, ingest_repo  # noqa: E402

DEMO = Path(__file__).resolve().parents[2] / "demo-target"
REPO = DEMO / "build" / "paystream"
FIXTURES = DEMO / "fixtures"
OWNER_EMAIL = "demo@roottrace.dev"


async def get_or_create_project(session) -> Project:
    owner = await session.scalar(select(User).where(User.email == OWNER_EMAIL))
    if owner is None:
        # Nobody knows this password; the demo login is set up separately.
        owner = User(email=OWNER_EMAIL, password_hash=await hash_password(secrets.token_urlsafe()))
        session.add(owner)
        await session.flush()
    project = await session.scalar(
        select(Project).where(Project.owner_id == owner.id, Project.name == "PayStream")
    )
    if project is None:
        project = Project(owner_id=owner.id, name="PayStream", description="Demo payments API")
        session.add(project)
        await session.flush()
    return project


async def main(ref: str) -> None:
    if not REPO.exists():
        sys.exit(f"{REPO} not found: run python demo-target/scripts/build_history.py first")
    git = ["git", "-C", str(REPO)]
    subprocess.run([*git, "checkout", "-q", ref], check=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True)

    router = get_router()
    async with SessionLocal() as session:
        project = await get_or_create_project(session)
        repo = await ingest_repo(session, router, project.id, REPO, sha.stdout.strip())
        incidents = json.loads((FIXTURES / "incidents.json").read_text(encoding="utf-8"))
        issues = json.loads((FIXTURES / "issues.json").read_text(encoding="utf-8"))
        n_incidents = await ingest_incidents(session, router, project.id, incidents)
        n_issues = await ingest_issues(session, router, project.id, issues)
        n_logs = 0
        for file in sorted((FIXTURES / "logs").glob("*.jsonl")):
            rows = [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]
            n_logs += await ingest_logs(session, project.id, rows)
        await session.commit()

    tokens = sum(s["prompt_tokens"] for s in router.spans)
    cost = sum(s["cost_usd"] for s in router.spans)
    print(f"project {project.id} (PayStream at {ref}, {sha.stdout.strip()[:7]})")
    print(
        f"  repo files:  {repo['indexed']} indexed, {repo['unchanged']} unchanged, "
        f"{repo['removed']} removed"
    )
    print(f"  incidents:   {n_incidents} indexed of {len(incidents)}")
    print(f"  issues:      {n_issues} indexed of {len(issues)}")
    print(f"  log lines:   {n_logs}")
    print(f"  embeddings:  {len(router.spans)} calls, {tokens} tokens, ${cost:.6f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="v1.4.8", help="PayStream tag or branch to index")
    asyncio.run(main(parser.parse_args().ref))
