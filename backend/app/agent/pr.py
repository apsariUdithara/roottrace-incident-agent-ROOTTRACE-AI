"""Open a draft pull request for a verified fix, after an engineer approves it.

The agent never pushes code by itself. Its run ends with the investigation awaiting approval;
this is the separate, human-triggered step. Everything it needs is already saved: the patch's
diff, title and body. It:

1. checks the patch is verified and not already opened (so a double click opens one PR),
2. applies the diff to a temporary worktree at the deployed version and commits it,
3. pushes a branch and opens a DRAFT pull request against the release branch that contains the
   deployed version (the code that actually has the bug), not the default branch.
"""

import asyncio
import base64
import re
import tempfile
import uuid
from pathlib import Path

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.events import EventBus
from app.models import (
    Investigation,
    InvestigationStatus,
    Patch,
    PatchStatus,
    Repository,
)
from app.schemas.events import PrOpened, StatusChanged

GITHUB_API = "https://api.github.com"
AUTHOR = ["-c", "user.name=RootTrace", "-c", "user.email=roottrace@users.noreply.github.com"]


class PrError(Exception):
    """The PR can't be opened; the message says why (never contains the token)."""


async def _git(repo: Path, *args: str, secret: str = "") -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        message = err.decode("utf-8", "replace").strip()
        raise PrError(
            f"git {args[0]} failed: {message.replace(secret, '***') if secret else message}"
        )
    return out.decode("utf-8", "replace")


def owner_and_name(url: str) -> tuple[str, str]:
    m = re.match(r"https://github\.com/([^/]+)/([^/.]+)", url)
    if not m:
        raise PrError(f"not a GitHub repository URL: {url}")
    return m.group(1), m.group(2)


async def base_branch(repo: Path, ref: str, default: str) -> str:
    """The branch that contains the deployed version: a release branch if there is one."""
    out = await _git(
        repo,
        "for-each-ref",
        "--contains",
        ref,
        "--format=%(refname:short)",
        "refs/heads",
        "refs/remotes",
    )
    names = [n.removeprefix("origin/") for n in out.split() if n not in ("origin", "origin/HEAD")]
    release = [n for n in dict.fromkeys(names) if n != default]
    return release[0] if release else default


async def open_pr(
    session: AsyncSession,
    patch_id: uuid.UUID,
    token: str,
    *,
    remote_url: str | None = None,  # tests push to a local bare repository
    http: httpx2.AsyncClient | None = None,  # tests pass a mock transport
) -> str:
    """Open the draft PR for a verified patch and return its URL."""
    patch = await session.get(Patch, patch_id)
    if patch is None:
        raise PrError("no such patch")
    if patch.pr_url:
        return patch.pr_url  # already opened: a second approval changes nothing
    if patch.status != PatchStatus.VERIFIED:
        raise PrError(f"only a verified fix can be opened as a PR (this one is {patch.status})")
    investigation = await session.get(Investigation, patch.investigation_id)
    repository = await session.scalar(
        select(Repository).where(Repository.project_id == investigation.project_id)
    )
    ref = (investigation.report or {}).get("deployed_ref")
    if not repository or not repository.local_path or not ref:
        raise PrError("the investigation has no repository or deployed version recorded")

    repo = Path(repository.local_path)
    owner, name = owner_and_name(repository.url)
    branch = f"roottrace/fix-{str(patch_id)[:8]}"
    base = await base_branch(repo, ref, repository.default_branch)
    remote = remote_url or f"https://github.com/{owner}/{name}.git"
    # The token goes in a header for this one push, and credential saving is switched off:
    # otherwise git stores it as the user's GitHub login, replacing their own.
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    no_saving = ["-c", "credential.helper=", "-c", f"http.extraHeader=Authorization: Basic {basic}"]

    with tempfile.TemporaryDirectory(prefix="roottrace-pr-") as tmp:
        worktree = Path(tmp) / "wt"
        await _git(repo, "worktree", "add", "--detach", str(worktree), ref)
        try:
            (Path(tmp) / "fix.patch").write_text(patch.diff, encoding="utf-8", newline="\n")
            await _git(worktree, "apply", str(Path(tmp) / "fix.patch"))
            await _git(worktree, "add", "-A")
            await _git(worktree, *AUTHOR, "commit", "-q", "-m", patch.pr_title or "RootTrace fix")
            await _git(
                worktree, *no_saving, "push", remote, f"HEAD:refs/heads/{branch}", secret=basic
            )
        finally:
            await _git(repo, "worktree", "remove", "--force", str(worktree))

    client = http or httpx2.AsyncClient(timeout=30)
    try:
        response = await client.post(
            f"{GITHUB_API}/repos/{owner}/{name}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "title": patch.pr_title,
                "body": patch.pr_body,
                "head": branch,
                "base": base,
                "draft": True,
            },
        )
    finally:
        if http is None:
            await client.aclose()
    if response.status_code != 201:
        raise PrError(f"GitHub refused the PR ({response.status_code}): {response.text[:300]}")

    patch.pr_url = response.json()["html_url"]
    investigation.status = InvestigationStatus.COMPLETED
    bus = await EventBus.resume(session, investigation.id)
    await bus.publish(PrOpened(patch_id=patch.id, url=patch.pr_url))
    await bus.publish(StatusChanged(status=InvestigationStatus.COMPLETED))
    return patch.pr_url
