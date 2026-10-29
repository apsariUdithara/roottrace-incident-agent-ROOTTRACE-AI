"""Opening an evidence source: a `source_ref` back into what it points to, for the source viewer.

Two kinds of ref reach GET /projects/{id}/sources:

- The app's own, from the Knowledge list and chat citations (`source_ref()` in
  app/schemas/knowledge.py): `file:app/db.py#L10-L20@<sha>`, `doc:docs/pool.md`,
  `incident:INC-017` and `issue:42`. Also `commit:<sha>`, `log:<trace id>` and `memory:<id>`.
- The agent's: a fact's `source_ref` is the `ref` of the tool result it was quoted from, such as
  `app/db.py L10-20 @ v1.4.8` (get_file), `docs/pool.md § Pool > Size L5-17 @ f89f6df` or
  `app/db.py :: connect L3-9 @ f89f6df` (search hits), `commit 5b0bb55c90`, `INC-017`,
  `log trace_id=4ecb5ffc3e4ec068` and `log deploy at 2026-10-06T14:20:00+00:00`.

Files, docs and commits are read from the repository's clone with git, at the version the ref
names (else the indexed commit), never from a working tree. The rest come from the database.
Everything is scoped to the project. SQL results (`sql: SELECT …`) aren't kept, so they don't
resolve: the fact's excerpt is what the query returned.
"""

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Document,
    DocumentChunk,
    DocumentSourceType,
    Incident,
    LogEntry,
    Memory,
    Project,
    Repository,
)
from app.schemas.incident import IncidentOut
from app.schemas.knowledge import (
    CommitSource,
    DocSource,
    FileSource,
    IncidentSource,
    IssueSource,
    LogEntryOut,
    LogSource,
    MemorySource,
    Source,
)
from app.schemas.memory import MemoryOut
from app.tools.git import safe_repo_path
from app.tools.registry import ToolError

GIT_TIMEOUT_S = 15
MAX_FILE_BYTES = 1_000_000  # the viewer shows a whole file
MAX_DIFF_CHARS = 200_000
MAX_LOG_LINES = 500

# Monaco's language ids, for highlighting. Anything else is shown as plain text.
LANGUAGES = {
    ".py": "python",
    ".md": "markdown",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ini": "ini",
    ".cfg": "ini",
    ".env": "shell",
    ".sh": "shell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}
LANGUAGE_BY_NAME = {"Dockerfile": "dockerfile", ".env": "shell"}

APP_FILE = re.compile(
    r"^file:(?P<path>[^#@]+)(?:#L(?P<start>\d+)(?:-L(?P<end>\d+))?)?(?:@(?P<rev>.+))?$"
)
APP_DOC = re.compile(r"^doc:(?P<path>[^#@]+)(?:#(?P<section>[^@]+))?(?:@(?P<rev>.+))?$")
# A search hit's citation or get_file's ref: path, then a section or a symbol, lines, version.
CITATION = re.compile(
    r"^(?P<path>.+?)(?: § (?P<section>.+?)| :: (?P<symbol>.+?))?"
    r"(?: L(?P<start>\d+)-(?P<end>\d+))?(?: @ (?P<rev>\S+))?$"
)
INCIDENT_KEY = re.compile(r"^[A-Za-z]+-\d+$")
SHA = re.compile(r"^[0-9a-fA-F]{4,40}$")
REV = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._/+-]*$")  # a sha, branch or tag; never an option
UNKNOWN_KIND = re.compile(r"^[a-z_]+:")
DOC_SUFFIXES = {".md", ".markdown"}


class SourceError(Exception):
    """Why a ref can't be opened, in words for the user, with the HTTP status to answer."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def not_found(detail: str) -> SourceError:
    return SourceError(404, detail)


@dataclass(frozen=True)
class Ref:
    """A parsed ref. `kind` is file, doc, path (a file or a doc: whichever it was indexed as),
    commit, incident, issue, log, deploy or memory."""

    kind: str
    value: str  # a path, sha, incident key, issue number, trace id, timestamp or memory id
    rev: str | None = None  # the version a file or doc is read at
    start_line: int | None = None
    end_line: int | None = None
    section: str | None = None  # a doc heading, as written or as a slug


def _int(text: str | None) -> int | None:
    return int(text) if text else None


def parse_ref(ref: str) -> Ref:
    """Read either kind of ref (see the module docstring). Raises SourceError for a ref that
    can never resolve."""
    ref = ref.strip()
    if not ref:
        raise not_found("The ref is empty")
    if ref.startswith("sql:"):
        raise not_found("Query results aren't kept: the fact's excerpt is what the query returned")
    if match := APP_FILE.match(ref):
        start = _int(match["start"])
        return Ref("file", match["path"], match["rev"], start, _int(match["end"]) or start)
    if match := APP_DOC.match(ref):
        return Ref("doc", match["path"], match["rev"], section=match["section"])
    for prefix, kind in (("commit:", "commit"), ("commit ", "commit"), ("incident:", "incident")):
        if ref.startswith(prefix):
            return Ref(kind, ref.removeprefix(prefix).strip())
    if ref.startswith("issue:"):
        return Ref("issue", ref.removeprefix("issue:").strip().lstrip("#"))
    for prefix, kind in (("log trace_id=", "log"), ("log:", "log"), ("log deploy at ", "deploy")):
        if ref.startswith(prefix):
            return Ref(kind, ref.removeprefix(prefix).strip())
    if ref.startswith("memory:"):
        return Ref("memory", ref.removeprefix("memory:").strip())
    if INCIDENT_KEY.match(ref):
        return Ref("incident", ref)
    if UNKNOWN_KIND.match(ref):
        raise not_found(f"{ref} isn't a kind of source that can be opened")
    match = CITATION.match(ref)
    section = match["section"].split(" > ")[-1] if match["section"] else None
    start = _int(match["start"])
    return Ref("path", match["path"], match["rev"], start, _int(match["end"]), section)


def heading_slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to dashes. The
    viewer gives its headings the same ids, to scroll to a doc's section."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def language(path: str) -> str | None:
    name = PurePosixPath(path).name
    return LANGUAGE_BY_NAME.get(name) or LANGUAGES.get(PurePosixPath(name).suffix.lower())


async def resolve(session: AsyncSession, project: Project, ref: str) -> Source:
    """What `ref` points to in this project. Raises SourceError when it doesn't resolve (404),
    needs a repository that isn't indexed (409), or is a file too large or binary to show (422).
    """
    parsed = parse_ref(ref)
    match parsed.kind:
        case "incident":
            return await _incident(session, project.id, ref, parsed.value)
        case "issue":
            return await _issue(session, project.id, ref, parsed.value)
        case "log":
            return await _trace(session, project.id, ref, parsed.value)
        case "deploy":
            return await _deploy(session, project.id, ref, parsed.value)
        case "memory":
            return await _memory(session, project.id, ref, parsed.value)
    repo = await _repository(session, project.id)
    if parsed.kind == "commit":
        return await _commit_source(repo, ref, parsed.value)
    path = _safe_path(parsed.value)
    document = await session.scalar(
        select(Document)
        .where(Document.project_id == project.id, Document.path == path)
        .order_by(Document.source_type)  # code before doc, if a path were both
    )
    is_doc = parsed.kind == "doc" or (
        parsed.kind == "path"
        and (
            document.source_type == DocumentSourceType.DOC
            if document
            else PurePosixPath(path).suffix.lower() in DOC_SUFFIXES
        )
    )
    # Without a version, read what was indexed: the document's commit, else the repository's.
    rev = parsed.rev or (document.commit_sha if document else None)
    sha = await _resolve_rev(repo, rev)
    text = await _blob(repo, sha, path)
    if is_doc:
        return DocSource(
            kind="doc",
            ref=ref,
            path=path,
            section=heading_slug(parsed.section) if parsed.section else None,
            title=document.title if document else None,
            markdown=text,
        )
    start, end = parsed.start_line, parsed.end_line or parsed.start_line
    count = len(text.splitlines())
    if start is None or end is None or start > count:
        start = end = None  # lines that aren't in the file: show it without a highlight
    else:
        end = min(max(start, end), count)
    return FileSource(
        kind="file",
        ref=ref,
        path=path,
        sha=sha,
        language=language(path),
        content=text,
        start_line=start,
        end_line=end,
    )


# --- From the repository -----------------------------------------------------------------------


async def _repository(session: AsyncSession, project_id: uuid.UUID) -> Repository:
    repo = await session.scalar(
        select(Repository)
        .where(Repository.project_id == project_id)
        .order_by(Repository.created_at)
        .limit(1)
    )
    if repo is None or not repo.local_path:
        raise SourceError(409, "Index the project's repository first")
    if not Path(repo.local_path).exists():
        raise SourceError(409, "The repository's clone is missing: index it again")
    return repo


async def _git(repo: Repository, *args: str) -> bytes | None:
    """Run a read-only git command in the clone: its output, or None if git refused it (an
    unknown commit or path)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo.local_path),
        *args,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        raise SourceError(503, "Reading the repository took too long. Try again.") from None
    return out if proc.returncode == 0 else None


def _safe_path(path: str) -> str:
    try:
        return safe_repo_path(path)
    except ToolError as exc:
        raise not_found(str(exc)) from None


async def _resolve_rev(repo: Repository, rev: str | None) -> str:
    """The full sha of a version. None or HEAD means the indexed one: the clone's HEAD can be
    a different branch from the one that was indexed."""
    if rev in (None, "HEAD"):
        rev = repo.last_indexed_sha or repo.default_branch
    if not REV.match(rev) or ".." in rev:
        raise not_found(f"{rev} isn't a version of this repository")
    out = await _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if not out:
        raise not_found(f"{rev} isn't a version of this repository")
    return out.decode().strip()


async def _blob(repo: Repository, sha: str, path: str) -> str:
    """A file's text at a commit."""
    listing = await _git(repo, "ls-tree", "-l", sha, "--", path)
    if not listing:
        raise not_found(f"{path} doesn't exist at {sha[:7]}")
    _mode, kind, obj, size = listing.decode("utf-8", "replace").partition("\t")[0].split()
    if kind != "blob":
        raise not_found(f"{path} is a folder, not a file")
    if int(size) > MAX_FILE_BYTES:
        raise SourceError(422, f"{path} is too large to show ({int(size) // 1000} kB)")
    content = await _git(repo, "cat-file", "blob", obj) or b""
    if b"\0" in content:
        raise SourceError(422, f"{path} is a binary file")
    return content.decode("utf-8", "replace")


async def _commit_source(repo: Repository, ref: str, sha: str) -> CommitSource:
    if not SHA.match(sha):
        raise not_found(f"{sha} isn't a commit sha")
    full = await _resolve_rev(repo, sha)
    header = await _git(repo, "show", "-s", "--format=%H%x1f%an%x1f%cI%x1f%B", full) or b""
    _sha, author, committed_at, message = header.decode("utf-8", "replace").split("\x1f", 3)
    files = await _git(repo, "show", "--format=", "--name-only", full) or b""
    diff = (await _git(repo, "show", "--format=", "--patch", "--no-color", full) or b"").decode(
        "utf-8", "replace"
    )
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated]\n"
    return CommitSource(
        kind="commit",
        ref=ref,
        sha=full,
        message=message.strip(),
        author=author,
        committed_at=datetime.fromisoformat(committed_at),
        files=files.decode("utf-8", "replace").split(),
        diff=diff,
    )


# --- From the database -------------------------------------------------------------------------


async def _incident(
    session: AsyncSession, project_id: uuid.UUID, ref: str, key: str
) -> IncidentSource:
    incident = await session.scalar(
        select(Incident).where(Incident.project_id == project_id, Incident.key == key.upper())
    )
    if incident is None:
        raise not_found(f"This project has no incident {key.upper()}")
    return IncidentSource(kind="incident", ref=ref, incident=IncidentOut.model_validate(incident))


ISSUE_STATE = re.compile(r"^Issue #\d+ \((open|closed)\b")


async def _issue(
    session: AsyncSession, project_id: uuid.UUID, ref: str, number: str
) -> IssueSource:
    """An issue from the index: ingest keeps one chunk per issue, "Issue #<n> (<state>, <date>)",
    then the title, then the body. Labels aren't indexed, so there are none."""
    if not number.isdigit():
        raise not_found(f"This project has no issue #{number}")
    row = (
        await session.execute(
            select(Document, DocumentChunk.content)
            .join(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(
                Document.project_id == project_id,
                Document.source_type == DocumentSourceType.ISSUE,
                Document.path == f"issue:{number}",
            )
            .limit(1)
        )
    ).first()
    if row is None:
        raise not_found(f"This project has no issue #{number}")
    document, content = row
    header, _, rest = content.partition("\n")
    title, _, body = rest.partition("\n")
    state = match[1] if (match := ISSUE_STATE.match(header)) else "open"
    repo = await session.scalar(
        select(Repository).where(Repository.project_id == project_id).limit(1)
    )
    return IssueSource(
        kind="issue",
        ref=ref,
        number=int(number),
        title=document.title or title,
        body=body,
        state=state,
        labels=[],
        url=f"{repo.url.removesuffix('.git')}/issues/{number}" if repo else "",
    )


async def _log_lines(session: AsyncSession, project_id: uuid.UUID, *where) -> list[LogEntryOut]:
    entries = await session.scalars(
        select(LogEntry)
        .where(LogEntry.project_id == project_id, *where)
        .order_by(LogEntry.ts, LogEntry.id)
        .limit(MAX_LOG_LINES)
    )
    return [LogEntryOut.model_validate(entry) for entry in entries]


async def _trace(
    session: AsyncSession, project_id: uuid.UUID, ref: str, trace_id: str
) -> LogSource:
    entries = await _log_lines(session, project_id, LogEntry.trace_id == trace_id)
    if not entries:
        raise not_found(f"No log lines have the trace id {trace_id}")
    return LogSource(kind="log", ref=ref, trace_id=trace_id, entries=entries)


async def _deploy(session: AsyncSession, project_id: uuid.UUID, ref: str, when: str) -> LogSource:
    """The deploy lines logged at that moment, as search_logs reports them."""
    try:
        ts = datetime.fromisoformat(when)
    except ValueError:
        raise not_found(f"{when} isn't a time") from None
    ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    entries = await _log_lines(
        session, project_id, LogEntry.ts == ts, LogEntry.message.ilike("deploy%")
    )
    if not entries:
        raise not_found(f"No deploy was logged at {when}")
    return LogSource(kind="log", ref=ref, trace_id=entries[0].trace_id, entries=entries)


async def _memory(
    session: AsyncSession, project_id: uuid.UUID, ref: str, memory_id: str
) -> MemorySource:
    try:
        key = uuid.UUID(memory_id)
    except ValueError:
        raise not_found(f"This project has no memory {memory_id}") from None
    memory = await session.scalar(
        select(Memory).where(Memory.project_id == project_id, Memory.id == key)
    )
    if memory is None:
        raise not_found(f"This project has no memory {memory_id}")
    return MemorySource(kind="memory", ref=ref, memory=MemoryOut.model_validate(memory))
