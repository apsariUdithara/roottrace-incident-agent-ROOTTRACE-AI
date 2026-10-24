"""The MCP servers, through a real MCP client connected in-process (no network, no credits)."""

from contextlib import asynccontextmanager

import pytest
from mcp import Client

from app.mcp_servers import AccessDenied, authorize, build_server
from app.tools import ToolContext
from tests.test_retrieve import FakeRouter
from tests.test_tools import repo  # noqa: F401 - pytest fixture


@pytest.fixture
def fixed_context(db_session, project, repo):  # noqa: F811
    @asynccontextmanager
    async def context(_ctx):
        yield ToolContext(db_session, FakeRouter(), project.id, repo, ref="v2")

    return context


async def test_each_server_lists_its_tools_with_their_schemas(fixed_context):
    async with Client(build_server("incidents", fixed_context)) as client:
        tools = (await client.list_tools()).tools
    assert [t.name for t in tools] == [
        "search_logs",
        "search_previous_incidents",
        "get_incident",
        "query_database",
    ]
    assert "since" in tools[0].input_schema["properties"]  # the registry's own schema


async def test_a_tool_call_runs_the_real_tool(fixed_context):
    async with Client(build_server("github", fixed_context)) as client:
        result = await client.call_tool("get_file", {"path": "config.py"})
    assert not result.is_error and "DB_POOL_SIZE = 100" in result.content[0].text


async def test_guards_still_apply_through_mcp(fixed_context):
    async with Client(build_server("github", fixed_context)) as client:
        result = await client.call_tool("get_file", {"path": "../../.env"})
    assert result.is_error and "may not contain '..'" in result.content[0].text


async def test_a_server_only_offers_its_own_tools(fixed_context):
    async with Client(build_server("docs", fixed_context)) as client:
        result = await client.call_tool("query_database", {"sql": "SELECT 1"})
    assert result.is_error and "unknown tool" in result.content[0].text


@pytest.mark.parametrize(
    ("header", "expected", "allowed"),
    [
        ("Bearer s3cret", "s3cret", True),
        ("bearer s3cret", "s3cret", True),
        ("Bearer wrong", "s3cret", False),
        (None, "s3cret", False),
        ("Bearer anything", None, False),  # MCP_TOKEN unset: everything is refused
    ],
)
def test_bearer_token_check(header, expected, allowed):
    if allowed:
        authorize(header, expected)
    else:
        with pytest.raises(AccessDenied):
            authorize(header, expected)
