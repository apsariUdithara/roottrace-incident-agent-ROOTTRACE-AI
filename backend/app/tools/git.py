"""Git tools: commit history, diffs and file contents at the deployed version.

Everything is read through `git` at `ctx.ref`, never from the working tree, so the agent sees
exactly the code that was deployed and cannot read files outside the repository.
"""

import asyncio
import re
from datetime import date

from pydantic import BaseModel, Field, field_validator

from app.tools.registry import ToolContext, ToolError, tool

GIT_TIMEOUT_S = 15
MAX_FILE_LINES = 200
MAX_DIFF_CHARS = 6000
MAX_COMMITS = 20


async def git(ctx: ToolContext, *args: str) -> str:
    """Run a read-only git command in the project's repository and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(ctx.repo_path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        raise ToolError("git took too long; narrow the request") from None
    # `git grep` exits 1 when nothing matches: that is an empty result, not an error.
    if proc.returncode not in (0, 1) or (proc.returncode == 1 and args[0] != "grep"):
        raise ToolError(err.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return out.decode("utf-8", "replace")


def safe_repo_path(path: str) -> str:
    """Accept only a relative path inside the repository."""
    clean = path.strip().replace("\\", "/").removeprefix("./")
    if not clean or clean.startswith("/") or re.match(r"^[A-Za-z]:", clean):
        raise ToolError("path must be relative to the repository root, e.g. src/app.py")
    if ".." in clean.split("/"):
        raise ToolError("path may not contain '..'")
    return clean


class GetFileArgs(BaseModel):
    path: str = Field(description="File path relative to the repository root.")
    start_line: int = Field(default=1, ge=1, description="First line to return (1-based).")
    end_line: int | None = Field(
        default=None, ge=1, description=f"Last line to return. At most {MAX_FILE_LINES} lines."
    )


@tool(
    "get_file",
    "Read a file from the repository at the deployed version, with line numbers. Use it to read "
    "the exact code or config a search result points to. Long files: request a line range.",
    GetFileArgs,
)
async def get_file(ctx: ToolContext, args: GetFileArgs) -> dict:
    path = safe_repo_path(args.path)
    try:
        text = await git(ctx, "show", f"{ctx.ref}:{path}")
    except ToolError:
        raise ToolError(f"{path} does not exist at {ctx.ref}") from None
    lines = text.splitlines()
    end = min(args.end_line or len(lines), len(lines), args.start_line + MAX_FILE_LINES - 1)
    numbered = [f"{n:>4}  {lines[n - 1]}" for n in range(args.start_line, end + 1)]
    result = {"ref": f"{path} L{args.start_line}-{end} @ {ctx.ref}", "content": "\n".join(numbered)}
    if end < len(lines):
        result["note"] = f"file has {len(lines)} lines; request start_line={end + 1} for more"
    return result


class CommitSearchArgs(BaseModel):
    query: str | None = Field(
        default=None,
        max_length=200,
        description="Text to find in commit messages OR in the changed code, e.g. a setting "
        "name like DB_POOL_SIZE. Leave empty to list recent commits.",
    )
    since: date | None = Field(default=None, description="Only commits on or after this date.")
    until: date | None = Field(default=None, description="Only commits on or before this date.")

    @field_validator("since", "until", mode="before")
    @classmethod
    def _date_part(cls, value):
        return value[:10] if isinstance(value, str) else value  # accept full ISO timestamps

    path: str | None = Field(default=None, description="Only commits that touched this path.")


RECORD = "\x1e"  # separates commits in `git log` output
FIELD = "\x1f"  # separates fields within one commit


def _ere_escape(text: str) -> str:
    """Escape text for git's -G option (a POSIX extended regular expression)."""
    return re.sub(r"([.\[\]()*+?{}|^$\\])", r"\\\1", text)


async def _log(ctx: ToolContext, args: CommitSearchArgs, *selector: str) -> list[dict]:
    cmd = ["log", ctx.ref, f"-n{MAX_COMMITS}", "--date=iso-strict", "--numstat"]
    cmd.append(f"--format={RECORD}%H{FIELD}%an{FIELD}%ad{FIELD}%s")
    if args.since:
        cmd.append(f"--since={args.since.isoformat()}")
    if args.until:
        cmd.append(f"--until={args.until.isoformat()}T23:59:59")
    cmd += [*selector, "--"]
    if args.path:
        cmd.append(safe_repo_path(args.path))
    commits = []
    for record in (await git(ctx, *cmd)).split(RECORD)[1:]:
        header, *stat_lines = record.strip().splitlines()
        sha, author, when, subject = header.split(FIELD)
        files = []
        for line in stat_lines:
            if line.strip():
                added, removed, name = line.split("\t", 2)
                files.append(f"{name} (+{added} -{removed})")
        commits.append(
            {
                "ref": f"commit {sha[:10]}",
                "sha": sha[:10],
                "author": author,
                "date": when,
                "message": subject,
                "files": files,
            }
        )
    return commits


@tool(
    "search_github_commits",
    "List commits in the deployed version's history, newest first, with author, date, message "
    "and changed files. Filter by text (matches the message or the changed code), date or path. "
    "Commit messages can be misleading: confirm with get_commit_diff.",
    CommitSearchArgs,
)
async def search_github_commits(ctx: ToolContext, args: CommitSearchArgs) -> dict:
    if not args.query:
        commits = await _log(ctx, args)
    else:
        by_message = await _log(ctx, args, "-i", f"--grep={args.query}", "--fixed-strings")
        by_change = await _log(ctx, args, f"-G{_ere_escape(args.query)}")
        merged = {c["sha"]: c for c in by_message + by_change}
        commits = sorted(merged.values(), key=lambda c: c["date"], reverse=True)[:MAX_COMMITS]
    return {"ref": ctx.ref, "commits": commits}


class CommitArgs(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-fA-F]{4,40}$", description="Commit SHA (at least 4 chars).")


@tool(
    "get_commit_diff",
    "Show exactly what one commit changed: message, author, date and the full diff.",
    CommitArgs,
)
async def get_commit_diff(ctx: ToolContext, args: CommitArgs) -> dict:
    out = await git(ctx, "show", "--date=iso-strict", "--stat", "--patch", args.sha, "--")
    if len(out) > MAX_DIFF_CHARS:
        out = out[:MAX_DIFF_CHARS] + "\n... [diff truncated; use get_file for full files]"
    return {"ref": f"commit {args.sha[:10]}", "diff": out}
