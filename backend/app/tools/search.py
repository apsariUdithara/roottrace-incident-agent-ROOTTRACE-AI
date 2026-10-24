"""Search tools over the indexed knowledge: docs, code, past incidents and issues."""

import re

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import DocumentSourceType, Incident
from app.rag.retrieve import Hit, search
from app.tools.git import git
from app.tools.registry import ToolContext, ToolError, tool

SNIPPET_CHARS = 1500


class SearchArgs(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=300,
        description="What to look for. Use the words the source would use: error messages, "
        "setting names, function names, or a short description of the behaviour.",
    )
    k: int = Field(default=5, ge=1, le=10, description="How many results to return.")


def _hits(hits: list[Hit]) -> list[dict]:
    return [{"ref": h.citation, "content": h.content[:SNIPPET_CHARS]} for h in hits]


async def _search(ctx: ToolContext, args: SearchArgs, source: DocumentSourceType) -> list[dict]:
    hits = await search(ctx.session, ctx.router, ctx.project_id, args.query, args.k, [source])
    return _hits(hits)


@tool(
    "search_documents",
    "Search the project's documentation (architecture, configuration rules, runbooks, API "
    "reference). Use it to find how something is supposed to work, limits and recommended "
    "values, and known caveats. Results cite file, section and lines.",
    SearchArgs,
)
async def search_documents(ctx: ToolContext, args: SearchArgs) -> dict:
    return {"results": await _search(ctx, args, DocumentSourceType.DOC)}


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{2,}$")


@tool(
    "search_code",
    "Search the source code and tests at the deployed version. Returns whole functions or "
    "classes with file and line numbers. If the query is a single identifier (e.g. "
    "DB_POOL_SIZE or calculate_total), also returns every exact line that mentions it.",
    SearchArgs,
)
async def search_code(ctx: ToolContext, args: SearchArgs) -> dict:
    result: dict = {"results": await _search(ctx, args, DocumentSourceType.CODE)}
    if IDENTIFIER.match(args.query):
        out = await git(ctx, "grep", "-n", "-I", "-F", "-e", args.query, ctx.ref, "--")
        result["exact_matches"] = [
            line.removeprefix(f"{ctx.ref}:") for line in out.splitlines()[:30]
        ]
    return result


@tool(
    "search_previous_incidents",
    "Search past incident reports for ones with similar symptoms. Each result has symptoms, "
    "root cause and fix. Similar symptoms do not mean the same cause: compare the root cause "
    "with the current evidence before relying on it.",
    SearchArgs,
)
async def search_previous_incidents(ctx: ToolContext, args: SearchArgs) -> dict:
    return {"results": await _search(ctx, args, DocumentSourceType.INCIDENT)}


@tool(
    "search_github_issues",
    "Search the project's issue tracker: bug reports, customer complaints and feature requests, "
    "with their dates. Useful for when a problem was first reported and how users describe it.",
    SearchArgs,
)
async def search_github_issues(ctx: ToolContext, args: SearchArgs) -> dict:
    # ponytail: searches the issues ingested into the index; a live GitHub API search can
    # replace this when projects connect a real GitHub token.
    return {"results": await _search(ctx, args, DocumentSourceType.ISSUE)}


class IncidentKey(BaseModel):
    key: str = Field(pattern=r"^[A-Z]+-\d+$", description="Incident key, e.g. INC-017.")


@tool(
    "get_incident",
    "Read one past incident in full by its key, e.g. INC-017.",
    IncidentKey,
)
async def get_incident(ctx: ToolContext, args: IncidentKey) -> dict:
    incident = await ctx.session.scalar(
        select(Incident).where(Incident.project_id == ctx.project_id, Incident.key == args.key)
    )
    if incident is None:
        raise ToolError(f"no incident {args.key}; use search_previous_incidents to find keys")
    return {
        "ref": incident.key,
        **{
            f: getattr(incident, f)
            for f in ("title", "severity", "symptoms", "root_cause", "fix", "commit_sha", "tags")
        },
        "occurred_at": incident.occurred_at,
    }
