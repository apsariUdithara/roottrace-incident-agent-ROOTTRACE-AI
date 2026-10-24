"""RootTrace's tools as MCP servers, usable by any MCP client (Claude Desktop, Cursor, ...).

Three servers, generated from the same tool registry the agent uses, so there is no second
copy of any tool and the same safety guards apply (read-only SQL, paths inside the repo):

    /mcp/github     commits, diffs, files, code and issues
    /mcp/docs       documentation and code search
    /mcp/incidents  logs, past incidents and the production data snapshot

A client connects to e.g. http://localhost:8000/mcp/incidents/?project=<id>&ref=v1.4.8 with
`Authorization: Bearer <MCP_TOKEN>`.
"""

import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from mcp import types
from mcp.server.lowlevel import Server
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.llm.router import get_router
from app.models import Repository
from app.tools import TOOLS, ToolContext, call_tool

SERVERS = {
    "github": [
        "search_github_commits",
        "get_commit_diff",
        "get_file",
        "search_code",
        "search_github_issues",
    ],
    "docs": ["search_documents", "search_code", "get_file"],
    "incidents": ["search_logs", "search_previous_incidents", "get_incident", "query_database"],
}

ContextFactory = Callable[[object], AbstractAsyncContextManager[ToolContext]]


class AccessDenied(Exception):
    pass


def authorize(authorization: str | None, expected: str | None) -> None:
    """Bearer-token check. MCP stays disabled until MCP_TOKEN is configured."""
    # ponytail: one shared token for now; per-user, per-project tokens once login is wired in.
    if not expected:
        raise AccessDenied("MCP is disabled: set MCP_TOKEN on the server")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected):
        raise AccessDenied("invalid or missing bearer token")


@asynccontextmanager
async def http_context(ctx) -> AsyncIterator[ToolContext]:
    """Tool context for an HTTP request: token check, then the project and version it names."""
    request = ctx.request
    token = get_settings().mcp_token
    authorize(request.headers.get("authorization"), token.get_secret_value() if token else None)
    try:
        project_id = uuid.UUID(request.query_params.get("project", ""))
    except ValueError:
        raise AccessDenied("add ?project=<project id> to the server URL") from None
    async with SessionLocal() as session:
        repo = await session.scalar(select(Repository).where(Repository.project_id == project_id))
        if repo is None or not repo.local_path:
            raise AccessDenied("unknown project, or it has no indexed repository")
        ref = request.query_params.get("ref") or repo.default_branch
        yield ToolContext(session, get_router(), project_id, Path(repo.local_path), ref=ref)


def build_server(name: str, context: ContextFactory = http_context) -> Server:
    tool_names = SERVERS[name]

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t,
                    description=TOOLS[t].description,
                    input_schema=TOOLS[t].args.model_json_schema(),
                )
                for t in tool_names
            ]
        )

    async def run_tool(ctx, params) -> types.CallToolResult:
        def result(text: str, error: bool) -> types.CallToolResult:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text)], is_error=error
            )

        if params.name not in tool_names:
            return result(f"unknown tool {params.name!r} on the {name} server", True)
        try:
            async with context(ctx) as tool_context:
                text = await call_tool(tool_context, params.name, params.arguments or {})
        except AccessDenied as exc:
            return result(str(exc), True)
        return result(text, not tool_context.trace[-1]["ok"])

    return Server(
        f"roottrace-{name}",
        version="1.0",
        instructions=f"RootTrace {name} tools for incident investigation. All tools are read-only.",
        on_list_tools=list_tools,
        on_call_tool=run_tool,
    )


class McpMount:
    """An ASGI app for /mcp/<name> that builds a fresh MCP server each time the API starts.

    An MCP session manager can only run once, and the API may start more than once in a process
    (the test suite starts it per test), so the server is created in `running()`, not at import.
    """

    def __init__(self, name: str):
        self.name, self.inner = name, None

    async def __call__(self, scope, receive, send):
        if self.inner is None:
            raise RuntimeError(f"MCP server {self.name!r} is not running")
        await self.inner(scope, receive, send)

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        server = build_server(self.name)
        self.inner = server.streamable_http_app(streamable_http_path="/")
        try:
            async with server.session_manager.run():
                yield
        finally:
            self.inner = None


MOUNTS = {name: McpMount(name) for name in SERVERS}
