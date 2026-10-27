"""Indexing a connected repository from the app (POST /projects/{id}/ingest).

One run, in the background:
1. Clone the repository (bare, into <data_dir>/repos/<id>.git), or fetch what's new. A
   repository without a remote, like the demo's local PayStream build, is used as it is.
2. Export its files at the default branch with `git archive`. The clone itself never changes,
   so an investigation can keep reading it meanwhile.
3. Index them with ingest_repo (unchanged files cost nothing), and record the commit.

Progress is the repository's `ingest_status`: queued, running, then indexed or failed (with
`ingest_error`). Runs live in this process, like investigations, so a restart interrupts them:
mark_interrupted() records that at startup.
"""

import asyncio
import base64
import logging
import os
import shutil
import stat
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import openai
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.fix import snapshot
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import decrypt_secret
from app.llm.router import LLMRouter
from app.models import IngestStatus, Repository
from app.rag.ingest import CONFIG_SUFFIXES, SKIP_DIRS, ingest_repo

logger = logging.getLogger(__name__)

GIT_TIMEOUT_S = 300  # cloning a large repository takes a while
# Files ingest_repo would chunk and embed. More than this would spend too much of a demo's
# budget on one repository.
INDEXABLE = {".md", ".py", *CONFIG_SUFFIXES}
MAX_FILES = 2000

# Repository id -> its indexing run; None while the request that claimed it is still saving.
RUNNING: dict[uuid.UUID, asyncio.Task | None] = {}


class IndexingError(Exception):
    """Why indexing failed, in words for the user, e.g. the repository wasn't found."""


def repos_dir() -> Path:
    return get_settings().data_dir / "repos"


def remote_url(url: str) -> str:
    return url if url.endswith(".git") else f"{url}.git"


def claim(repository_id: uuid.UUID) -> bool:
    """Reserve a run, before the caller awaits anything, so two clicks can't start two runs.
    False if one is already going. Follow with start(), or release() if saving fails."""
    if repository_id in RUNNING:
        return False
    RUNNING[repository_id] = None
    return True


def release(repository_id: uuid.UUID) -> None:
    RUNNING.pop(repository_id, None)


def start(repository_id: uuid.UUID, router: LLMRouter) -> None:
    """Index in the background. The repository must already be saved as queued."""
    task = asyncio.create_task(run(repository_id, router))
    RUNNING[repository_id] = task
    task.add_done_callback(lambda _: release(repository_id))


def mark_indexed(repository: Repository, sha: str) -> None:
    repository.ingest_status = IngestStatus.INDEXED
    repository.ingest_error = None
    repository.last_indexed_sha = sha
    repository.last_indexed_at = datetime.now(UTC)


async def mark_interrupted(session: AsyncSession) -> None:
    """At startup: runs still queued or running were cut off when the process stopped."""
    await session.execute(
        update(Repository)
        .where(Repository.ingest_status.in_([IngestStatus.QUEUED, IngestStatus.RUNNING]))
        .values(
            ingest_status=IngestStatus.FAILED,
            ingest_error="Indexing was interrupted by a server restart. Start it again.",
        )
    )
    await session.commit()


async def run(
    repository_id: uuid.UUID,
    router: LLMRouter,
    sessions: async_sessionmaker[AsyncSession] = SessionLocal,
) -> None:
    async with sessions() as session:
        repository = await session.get(Repository, repository_id)
        if repository is None:
            return  # disconnected meanwhile
        repository.ingest_status = IngestStatus.RUNNING
        await session.commit()
        try:
            await index(session, router, repository)
            await session.commit()  # the files, the commit and "indexed" together
            return
        except Exception as exc:  # recorded on the repository, for the user to see
            failure = exc
        await session.rollback()  # none of the half-indexed files
        repository = await session.get(Repository, repository_id, populate_existing=True)
        if repository is None:
            return  # disconnected meanwhile, which is also why the commit failed
        repository.ingest_status = IngestStatus.FAILED
        repository.ingest_error = reason_for(repository_id, failure)
        await session.commit()


def reason_for(repository_id: uuid.UUID, failure: Exception) -> str:
    if isinstance(failure, IndexingError):
        return str(failure)
    if isinstance(failure, (openai.APIError, TimeoutError)):
        logger.warning("indexing %s: the embedding model failed: %r", repository_id, failure)
        return "The embedding model didn't answer. Start the indexing again in a moment."
    logger.error("indexing %s failed", repository_id, exc_info=failure)
    return "Indexing failed unexpectedly. Start it again, or check the server log."


async def index(session: AsyncSession, router: LLMRouter, repository: Repository) -> None:
    token = decrypt_secret(repository.encrypted_token) if repository.encrypted_token else None
    clone = await local_clone(repository, token)
    sha = await resolve(clone, repository.default_branch)
    with tempfile.TemporaryDirectory(prefix="roottrace-index-") as tmp:
        files = Path(tmp)
        await asyncio.to_thread(snapshot, clone, sha, files)
        count = sum(1 for _ in indexable_files(files))
        if count > MAX_FILES:
            raise IndexingError(
                f"The repository has {count} files to index, and the limit is {MAX_FILES}."
            )
        await ingest_repo(session, router, repository.project_id, files, sha)
    repository.local_path = str(clone)  # from now on the agent can read it
    mark_indexed(repository, sha)


def indexable_files(root: Path):
    for file in root.rglob("*"):
        if file.is_file() and file.suffix in INDEXABLE:
            if not SKIP_DIRS & set(file.relative_to(root).parts):
                yield file


async def local_clone(repository: Repository, token: str | None) -> Path:
    """The repository's clone, up to date. Clones it the first time.

    Only RootTrace's own clone directory is ever created or removed: if a recorded local_path
    (e.g. the demo's build) is gone, the repository is cloned there instead."""
    own = repos_dir() / f"{repository.id}.git"
    for path in (Path(repository.local_path) if repository.local_path else None, own):
        if path and await is_git_repository(path):
            if "origin" in (await git(path, "remote")).split():
                await git(path, "fetch", "--quiet", "--prune", "--tags", "origin", token=token)
            return path
    path = own
    if path.exists():
        await asyncio.to_thread(remove_tree, path)  # a clone cut off halfway
    path.parent.mkdir(parents=True, exist_ok=True)
    url = remote_url(repository.url)
    await git(None, "clone", "--quiet", "--bare", url, str(path), token=token)
    # A bare clone has no fetch rule: keep its branches in step with GitHub's on each fetch.
    await git(path, "config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*")
    return path


async def is_git_repository(path: Path) -> bool:
    """True if `path` itself is a repository (bare, or with a .git), not just a folder inside one:
    in development, data/ is inside RootTrace's own repository."""
    if not path.is_dir():
        return False
    try:
        git_dir = Path((await git(path, "rev-parse", "--absolute-git-dir")).strip())
    except IndexingError:
        return False
    return git_dir.resolve() in (path.resolve(), (path / ".git").resolve())


async def resolve(clone: Path, branch: str) -> str:
    if branch.startswith("-"):  # never let a branch name become a git option
        raise IndexingError(f"{branch} isn't a valid branch name.")
    try:
        return (
            await git(clone, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}")
        ).strip()
    except IndexingError:
        raise IndexingError(
            f"The branch {branch} wasn't found in the repository. "
            "Check the default branch in the project settings."
        ) from None


async def git(repo: Path | None, *args: str, token: str | None = None) -> str:
    """Run git without ever prompting. The token goes in a header for this one command, through
    the environment: it's never written to disk or shown in a process list."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    config = [("credential.helper", "")]  # don't use or save the server's own git logins
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        config.append(("http.extraHeader", f"Authorization: Basic {basic}"))
    env["GIT_CONFIG_COUNT"] = str(len(config))
    for i, (key, value) in enumerate(config):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    cwd = ["-C", str(repo)] if repo else []
    proc = await asyncio.create_subprocess_exec(
        "git",
        *cwd,
        *args,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        raise IndexingError(f"git {args[0]} took longer than {GIT_TIMEOUT_S} s.") from None
    if proc.returncode != 0:
        raise IndexingError(git_failure(args[0], err.decode("utf-8", "replace"), token))
    return out.decode("utf-8", "replace")


def git_failure(command: str, stderr: str, token: str | None) -> str:
    if token:
        stderr = stderr.replace(token, "***")
    if any(
        sign in stderr
        for sign in ("Repository not found", "could not read Username", "Authentication failed")
    ):
        return (
            "GitHub didn't let RootTrace read the repository. Check the URL, and for a private "
            "repository, add an access token that can read it."
        )
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return f"git {command} failed: {lines[-1] if lines else 'no details'}"


def remove_tree(path: Path) -> None:
    """Like shutil.rmtree, but also removes git's read-only pack files on Windows."""

    def make_writable_and_retry(function, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=make_writable_and_retry)


def remove_clone(repository: Repository) -> None:
    """After a disconnect: delete the clone, if RootTrace made it (never the demo's own repo)."""
    if repository.local_path:
        path = Path(repository.local_path).resolve()
        if path.is_relative_to(repos_dir().resolve()) and path.exists():
            remove_tree(path)
