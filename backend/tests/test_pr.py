"""Opening a draft PR after approval: a local bare repo plays GitHub's git, a mock plays its API."""

import json
import subprocess

import httpx2
import pytest
from sqlalchemy import select

from app.agent.fix import FileChange, snapshot, unified_diff
from app.agent.pr import PrError, open_pr
from app.models import (
    Investigation,
    InvestigationEvent,
    InvestigationStatus,
    Patch,
    PatchStatus,
    Repository,
)
from tests.test_tools import repo  # noqa: F401 - pytest fixture

TOKEN = "github_pat_secret123"


class FakeGitHub:
    def __init__(self, status=201):
        self.status, self.requests = status, []

    def client(self):
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle))

    def handle(self, request):
        self.requests.append(json.loads(request.content))
        if self.status != 201:
            return httpx2.Response(self.status, json={"message": "Validation Failed"})
        return httpx2.Response(201, json={"html_url": "https://github.com/acme/paystream/pull/7"})


@pytest.fixture
async def approved(db_session, project, repo, tmp_path):  # noqa: F811
    """A verified patch for the test repo, plus a bare repository to push to."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    db_session.add(
        Repository(
            project_id=project.id, url="https://github.com/acme/paystream", local_path=str(repo)
        )
    )
    inv = Investigation(
        project_id=project.id,
        user_id=project.owner_id,
        question="Why?",
        status=InvestigationStatus.AWAITING_APPROVAL,
        report={"deployed_ref": "v2"},
    )
    db_session.add(inv)
    await db_session.flush()
    base = tmp_path / "base"
    snapshot(repo, "v2", base)
    diff = unified_diff(
        base, [FileChange(path="config.py", content="DB_POOL_SIZE = 20\nINSTANCES = 5\n")]
    )
    patch = Patch(
        investigation_id=inv.id,
        attempt=1,
        diff=diff,
        status=PatchStatus.VERIFIED,
        pr_title="fix(db): restore pool size",
        pr_body="## Root cause",
    )
    db_session.add(patch)
    await db_session.flush()
    return patch, inv, remote


async def test_approval_pushes_a_branch_and_opens_a_draft_pr(db_session, approved):
    patch, inv, remote = approved
    github = FakeGitHub()
    url = await open_pr(db_session, patch.id, TOKEN, remote_url=str(remote), http=github.client())
    assert url == "https://github.com/acme/paystream/pull/7" and patch.pr_url == url
    [request] = github.requests
    branch = f"roottrace/fix-{str(patch.id)[:8]}"
    assert request == {
        "title": "fix(db): restore pool size",
        "body": "## Root cause",
        "head": branch,
        "base": "main",
        "draft": True,
    }
    pushed = subprocess.run(
        ["git", "-C", str(remote), "show", f"{branch}:config.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert pushed.startswith("DB_POOL_SIZE = 20")
    assert inv.status == InvestigationStatus.COMPLETED
    events = (
        await db_session.scalars(
            select(InvestigationEvent.type).where(InvestigationEvent.investigation_id == inv.id)
        )
    ).all()
    assert events == ["pr_opened", "status"]


async def test_a_second_approval_does_not_open_a_second_pr(db_session, approved):
    patch, _, remote = approved
    github = FakeGitHub()
    first = await open_pr(db_session, patch.id, TOKEN, remote_url=str(remote), http=github.client())
    again = await open_pr(db_session, patch.id, TOKEN, remote_url=str(remote), http=github.client())
    assert again == first and len(github.requests) == 1


async def test_only_a_verified_fix_can_become_a_pr(db_session, approved):
    patch, _, remote = approved
    patch.status = PatchStatus.TESTS_FAILED
    with pytest.raises(PrError, match="only a verified fix"):
        await open_pr(
            db_session, patch.id, TOKEN, remote_url=str(remote), http=FakeGitHub().client()
        )


async def test_a_github_error_is_reported_without_the_token(db_session, approved):
    patch, _, remote = approved
    with pytest.raises(PrError) as error:
        await open_pr(
            db_session,
            patch.id,
            TOKEN,
            remote_url=str(remote),
            http=FakeGitHub(status=422).client(),
        )
    assert "422" in str(error.value) and TOKEN not in str(error.value)
    assert patch.pr_url is None
