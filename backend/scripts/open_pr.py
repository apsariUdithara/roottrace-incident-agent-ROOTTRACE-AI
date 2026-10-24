"""Approve the latest verified fix and open it as a DRAFT pull request on GitHub.

This is the human approval step: the agent never pushes code by itself. Run it only after
reading the patch and the PR draft printed by scripts/investigate.py.

Needs GITHUB_TOKEN in backend/.env: a fine-grained token for the demo repo only, with
Contents and Pull requests set to read and write.

Run from backend/:
    uv run python scripts/open_pr.py            # shows the patch, asks for confirmation
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from sqlalchemy import select  # noqa: E402

from app.agent.pr import PrError, open_pr  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.models import Investigation, Patch, PatchStatus, Project  # noqa: E402


async def main() -> None:
    token = get_settings().github_token
    if token is None:
        sys.exit("Add GITHUB_TOKEN to backend/.env first (see the docstring).")
    async with SessionLocal() as session:
        patch = await session.scalar(
            select(Patch)
            .join(Investigation, Patch.investigation_id == Investigation.id)
            .join(Project, Investigation.project_id == Project.id)
            .where(Project.name == "PayStream", Patch.status == PatchStatus.VERIFIED)
            .order_by(Patch.created_at.desc())
            .limit(1)
        )
        if patch is None:
            sys.exit("No verified fix yet: run scripts/investigate.py first.")
        if patch.pr_url:
            sys.exit(f"Already opened: {patch.pr_url}")
        print(f"PR title: {patch.pr_title}\n\n{patch.diff}")
        if input("Open this as a draft PR on GitHub? Type yes to approve: ").strip() != "yes":
            sys.exit("Not approved; nothing was sent.")
        try:
            url = await open_pr(session, patch.id, token.get_secret_value())
        except PrError as exc:
            sys.exit(f"Could not open the PR: {exc}")
        await session.commit()
        print(f"Draft PR opened: {url}")


asyncio.run(main())
