"""Insert the hand-written scenario-1 replay (app/demo/scenario1.py) into the demo project.

For building the Investigation page without credits or a sandbox: the replay is a finished
investigation, waiting for approval, whose events /events and /stream serve like a real run's.
Nothing is called, so it's free. Running it again replaces it. It can't be approved into a PR.

Run from backend/, after seed_demo.py (it needs the demo user's PayStream project):
    uv run python scripts/seed_fake_investigation.py
Write the events as JSON instead, for the frontend's tests (no database needed), to
frontend/src/test/fixtures/scenario-1-events.json:
    uv run python scripts/seed_fake_investigation.py --json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.demo import scenario1  # noqa: E402
from app.models import Project, User  # noqa: E402

OWNER_EMAIL = "demo@roottrace.dev"
FIXTURE = Path(__file__).resolve().parents[2] / "frontend/src/test/fixtures/scenario-1-events.json"


def events_json() -> str:
    events = [event.model_dump(mode="json") for event in scenario1.events()]
    return json.dumps(events, indent=2, ensure_ascii=False) + "\n"


async def main(project_name: str) -> None:
    async with SessionLocal() as session:
        project = await session.scalar(
            select(Project)
            .join(User, Project.owner_id == User.id)
            .where(User.email == OWNER_EMAIL, Project.name == project_name)
        )
        if project is None:
            sys.exit(
                f"No {project_name} project for {OWNER_EMAIL}: run scripts/seed_demo.py first."
            )
        investigation = await scenario1.seed(session, project)
    print(f"Replay of scenario 1 in {project.name}: investigation {investigation.id}")
    print(f"  {len(scenario1.events())} events, status {investigation.status.value}")
    print(f"  GET /investigations/{investigation.id}/stream replays them; nothing was spent")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="PayStream", help="the demo user's project")
    parser.add_argument(
        "--json", type=Path, nargs="?", const=FIXTURE, help="write the events as JSON instead"
    )
    args = parser.parse_args()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(events_json(), encoding="utf-8", newline="\n")
        print(f"wrote {len(scenario1.events())} events to {args.json}")
    else:
        asyncio.run(main(args.project))
