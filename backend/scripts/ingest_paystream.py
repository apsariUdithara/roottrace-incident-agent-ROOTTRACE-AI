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

from sqlalchemy import select, text  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.llm.router import get_router  # noqa: E402
from app.models import Project, Repository, User  # noqa: E402
from app.rag.indexing import mark_indexed  # noqa: E402
from app.rag.ingest import ingest_incidents, ingest_issues, ingest_logs, ingest_repo  # noqa: E402

DEMO = Path(__file__).resolve().parents[2] / "demo-target"
REPO = DEMO / "build" / "paystream"
FIXTURES = DEMO / "fixtures"
OWNER_EMAIL = "demo@roottrace.dev"


async def get_or_create_project(session, name: str = "PayStream") -> Project:
    owner = await session.scalar(select(User).where(User.email == OWNER_EMAIL))
    if owner is None:
        # Nobody knows this password; the demo login is set up separately.
        owner = User(email=OWNER_EMAIL, password_hash=await hash_password(secrets.token_urlsafe()))
        session.add(owner)
        await session.flush()
    project = await session.scalar(
        select(Project).where(Project.owner_id == owner.id, Project.name == name)
    )
    if project is None:
        project = Project(owner_id=owner.id, name=name, description=f"Demo payments API ({name})")
        session.add(project)
        await session.flush()
    return project


async def record_repository(session, project: Project, ref: str) -> Repository:
    """Remember where the clone lives, so the agent's git tools can find it. The indexed ref is
    its default branch: investigations start from it, and re-indexing from the app keeps it
    (PayStream's main is the healthy service, without the scenario's bug)."""
    repo = await session.scalar(select(Repository).where(Repository.project_id == project.id))
    if repo is None:
        repo = Repository(project_id=project.id, url="https://github.com/apsariUdithara/paystream")
        session.add(repo)
    repo.local_path = str(REPO)
    repo.default_branch = ref
    await session.flush()
    return repo


# ponytail: one shared demo_app schema for the single demo project; per-project schemas (or a
# real read replica) when there are several projects with production data.
PAYMENTS_SNAPSHOT = """
DROP SCHEMA IF EXISTS demo_app CASCADE;
CREATE SCHEMA demo_app;
CREATE TABLE demo_app.payments AS
    SELECT id, ts AS created_at, instance, status AS http_status, latency_ms, error_type
    FROM log_entries
    WHERE project_id = :project_id AND route = '/payments';
"""


async def load_production_snapshot(session, project: Project) -> int:
    """A small 'production' table for query_database, derived from the payment logs."""
    for statement in filter(str.strip, PAYMENTS_SNAPSHOT.split(";")):
        await session.execute(text(statement), {"project_id": project.id})
    return await session.scalar(text("SELECT count(*) FROM demo_app.payments"))


async def load(session, router, ref: str, name: str = "PayStream") -> tuple[Project, dict]:
    """Index PayStream at `ref` into project `name`. Re-runs only embed what changed."""
    if not REPO.exists():
        raise SystemExit(f"{REPO} not found: run python demo-target/scripts/build_history.py")
    git = ["git", "-C", str(REPO)]
    subprocess.run([*git, "checkout", "-q", ref], check=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    project = await get_or_create_project(session, name)
    repository = await record_repository(session, project, ref)
    repo = await ingest_repo(session, router, project.id, REPO, sha.stdout.strip())
    mark_indexed(repository, sha.stdout.strip())
    incidents = json.loads((FIXTURES / "incidents.json").read_text(encoding="utf-8"))
    issues = json.loads((FIXTURES / "issues.json").read_text(encoding="utf-8"))
    stats = {
        "sha": sha.stdout.strip(),
        "repo": repo,
        "incidents": (
            await ingest_incidents(session, router, project.id, incidents),
            len(incidents),
        ),
        "issues": (await ingest_issues(session, router, project.id, issues), len(issues)),
        "logs": 0,
    }
    for file in sorted((FIXTURES / "logs").glob("*.jsonl")):
        rows = [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]
        stats["logs"] += await ingest_logs(session, project.id, rows)
    stats["payments"] = await load_production_snapshot(session, project)
    await session.commit()
    return project, stats


async def main(ref: str) -> None:
    router = get_router()
    async with SessionLocal() as session:
        project, stats = await load(session, router, ref)
    repo = stats["repo"]
    tokens = sum(s["prompt_tokens"] for s in router.spans)
    cost = sum(s["cost_usd"] for s in router.spans)
    print(f"project {project.id} (PayStream at {ref}, {stats['sha'][:7]})")
    print(
        f"  repo files:  {repo['indexed']} indexed, {repo['unchanged']} unchanged, "
        f"{repo['removed']} removed"
    )
    print(f"  incidents:   {stats['incidents'][0]} indexed of {stats['incidents'][1]}")
    print(f"  issues:      {stats['issues'][0]} indexed of {stats['issues'][1]}")
    print(f"  log lines:   {stats['logs']}")
    print(f"  payments:    {stats['payments']} rows in demo_app.payments (for query_database)")
    print(f"  embeddings:  {len(router.spans)} calls, {tokens} tokens, ${cost:.6f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="v1.4.8", help="PayStream tag or branch to index")
    asyncio.run(main(parser.parse_args().ref))
