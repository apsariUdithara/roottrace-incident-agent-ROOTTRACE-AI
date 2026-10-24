"""Agent tools against the test database and a throwaway git repository (no credits)."""

import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models import DocumentSourceType, Incident, LogEntry
from app.rag.chunkers import Chunk
from app.rag.ingest import store_document
from app.tools import TOOLS, ToolContext, call_tool, database, openai_schemas
from tests.test_retrieve import FakeRouter

T0 = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)


def run_git(repo, *args, date="2026-10-01T10:00:00"):
    env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Dev", "-c", "user.email=dev@x.io", *args],
        check=True,
        capture_output=True,
        env={**__import__("os").environ, **env},
    )


@pytest.fixture
def repo(tmp_path):
    """Two commits: a healthy config, then an innocent-sounding change that breaks it."""
    run_git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "config.py").write_text("DB_POOL_SIZE = 20\nINSTANCES = 5\n")
    (tmp_path / "README.md").write_text("# Demo\n")
    run_git(tmp_path, "add", ".")
    run_git(tmp_path, "commit", "-q", "-m", "chore: initial config")
    (tmp_path / "config.py").write_text("DB_POOL_SIZE = 100\nINSTANCES = 5\n")
    run_git(
        tmp_path, "commit", "-q", "-am", "perf: tune for peak traffic", date="2026-10-06T14:02:00"
    )
    run_git(tmp_path, "tag", "v2")
    return tmp_path


@pytest.fixture
def ctx(db_session, project, repo):
    return ToolContext(db_session, FakeRouter(), project.id, repo, ref="v2")


async def call(ctx, name, **args):
    return json.loads(await call_tool(ctx, name, args))


# --- registry -------------------------------------------------------------------------------


def test_every_tool_has_a_description_and_schema_for_the_model():
    for schema in openai_schemas():
        fn = schema["function"]
        assert len(fn["description"]) > 40 and fn["parameters"]["type"] == "object"
    assert {"search_logs", "get_file", "query_database"} <= set(TOOLS)


async def test_bad_calls_come_back_as_errors_not_exceptions(ctx):
    assert "unknown tool" in (await call(ctx, "delete_everything"))["error"]
    assert "invalid arguments" in (await call(ctx, "get_commit_diff", sha="not-a-sha"))["error"]
    assert ctx.trace[-1]["ok"] is False


# --- git tools ------------------------------------------------------------------------------


async def test_get_file_reads_the_deployed_version_with_line_numbers(ctx):
    result = await call(ctx, "get_file", path="config.py")
    assert "   1  DB_POOL_SIZE = 100" in result["content"]
    assert result["ref"].startswith("config.py L1-2 @ v2")


@pytest.mark.parametrize(
    "path", ["../secrets.txt", "/etc/passwd", "C:/Windows/win.ini", "a/../../b"]
)
async def test_get_file_refuses_paths_outside_the_repository(ctx, path):
    assert "error" in await call(ctx, "get_file", path=path)


async def test_commit_search_finds_a_change_by_its_code_not_its_message(ctx):
    # The message says "tune for peak traffic"; only the diff mentions DB_POOL_SIZE.
    commits = (await call(ctx, "search_github_commits", query="DB_POOL_SIZE"))["commits"]
    assert [c["message"] for c in commits] == [
        "perf: tune for peak traffic",
        "chore: initial config",
    ]
    assert commits[0]["files"] == ["config.py (+1 -1)"]


async def test_commit_search_filters_by_date(ctx):
    commits = (await call(ctx, "search_github_commits", since="2026-10-05"))["commits"]
    assert len(commits) == 1


async def test_commit_diff_shows_the_change(ctx):
    sha = (await call(ctx, "search_github_commits", since="2026-10-05"))["commits"][0]["sha"]
    diff = (await call(ctx, "get_commit_diff", sha=sha))["diff"]
    assert "-DB_POOL_SIZE = 20" in diff and "+DB_POOL_SIZE = 100" in diff


# --- search tools ---------------------------------------------------------------------------


async def test_search_code_adds_exact_matches_for_identifiers(ctx):
    chunk = Chunk("DB_POOL_SIZE = 100", start_line=1, end_line=1, symbol="<module>")
    await store_document(
        ctx.session,
        ctx.router,
        ctx.project_id,
        DocumentSourceType.CODE,
        "config.py",
        None,
        "h",
        [chunk],
    )
    result = await call(ctx, "search_code", query="DB_POOL_SIZE")
    assert result["results"][0]["ref"].startswith("config.py")
    assert result["exact_matches"] == ["config.py:1:DB_POOL_SIZE = 100"]


async def test_get_incident_reads_one_record_and_explains_a_miss(ctx):
    ctx.session.add(
        Incident(project_id=ctx.project_id, key="INC-017", title="Pool", root_cause="60")
    )
    await ctx.session.flush()
    assert (await call(ctx, "get_incident", key="INC-017"))["root_cause"] == "60"
    assert "search_previous_incidents" in (await call(ctx, "get_incident", key="INC-999"))["error"]


# --- logs -----------------------------------------------------------------------------------


async def add_logs(ctx):
    for minute in range(0, 60, 2):
        ctx.session.add(
            LogEntry(
                project_id=ctx.project_id,
                ts=T0 + timedelta(minutes=minute),
                level="INFO",
                route="/payments",
                status=200,
                message="ok",
            )
        )
    for minute in (26, 30, 34):
        ctx.session.add(
            LogEntry(
                project_id=ctx.project_id,
                ts=T0 + timedelta(minutes=minute),
                level="ERROR",
                route="/payments",
                status=500,
                trace_id=f"t{minute}",
                error_type="OperationalError",
                message="too many connections",
            )
        )
    await ctx.session.flush()


async def test_search_logs_summarises_and_shows_when_errors_started(ctx):
    await add_logs(ctx)
    result = await call(ctx, "search_logs", since=T0.isoformat(), route="/payments")
    assert result["total"] == 33
    [error] = result["by_error_type"]
    assert error["count"] == 3 and error["first_seen"].startswith("2026-10-06T14:26")
    assert result["samples"][0]["level"] == "ERROR"  # errors first
    assert sum(b["count"] for b in result["warn_error_timeline"]) == 3


async def test_search_logs_with_no_match_says_how_to_widen(ctx):
    assert "widen" in (await call(ctx, "search_logs", text="nothing like this"))["note"]


# --- query_database ---------------------------------------------------------------------------


@pytest.fixture
async def production_data(ctx):
    s = ctx.session
    await s.execute(text("CREATE SCHEMA demo_app"))
    await s.execute(text("CREATE TABLE demo_app.payments (id int, http_status int)"))
    await s.execute(text("INSERT INTO demo_app.payments VALUES (1, 200), (2, 500), (3, 500)"))
    return ctx


async def test_query_database_answers_a_select(production_data):
    result = await call(
        production_data,
        "query_database",
        sql="SELECT http_status, count(*) AS n FROM payments GROUP BY 1 ORDER BY 1",
    )
    assert result["rows"] == [[200, 1], [500, 2]]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM payments",
        "DROP TABLE payments",
        "SELECT 1; DROP TABLE payments",
        "SELECT * FROM users",  # app table, outside demo_app
        "SELECT * FROM public.payments",  # schema prefix
        "SELECT pg_read_file('/etc/passwd')",  # unknown function
        "SELECT * INTO stolen FROM payments",
    ],
)
async def test_query_database_rejects_anything_but_a_safe_select(production_data, sql):
    assert "error" in await call(production_data, "query_database", sql=sql)


async def test_a_write_hidden_in_a_cte_is_refused(production_data):
    sql = "WITH gone AS (DELETE FROM payments RETURNING *) SELECT * FROM gone"
    assert "error" in await call(production_data, "query_database", sql=sql)
    count = await production_data.session.scalar(text("SELECT count(*) FROM demo_app.payments"))
    assert count == 3


async def test_read_only_transaction_stops_writes_even_if_the_parser_is_fooled(
    production_data, monkeypatch
):
    s = production_data.session
    await s.execute(
        text(
            "CREATE FUNCTION demo_app.wipe() RETURNS bigint LANGUAGE sql "
            "AS 'WITH d AS (DELETE FROM demo_app.payments RETURNING 1) SELECT count(*) FROM d'"
        )
    )
    monkeypatch.setattr(database, "check_sql", lambda sql, tables: "SELECT wipe() AS n")
    result = await call(production_data, "query_database", sql="SELECT 1")
    assert "read-only transaction" in result["error"]
    assert await s.scalar(text("SELECT count(*) FROM demo_app.payments")) == 3


async def test_read_only_mode_ends_with_the_query(production_data):
    await call(production_data, "query_database", sql="SELECT 1 AS one FROM payments")
    await production_data.session.execute(text("INSERT INTO demo_app.payments VALUES (4, 200)"))


async def test_commit_search_accepts_full_timestamps_and_an_end_date(ctx):
    found = await call(ctx, "search_github_commits", since="2026-10-06T14:00:00Z")
    assert len(found["commits"]) == 1
    none = await call(ctx, "search_github_commits", since="2026-09-01", until="2026-09-30")
    assert none["commits"] == []


async def test_search_logs_matches_part_of_a_route(ctx):
    await add_logs(ctx)
    assert (await call(ctx, "search_logs", route="payment"))["total"] == 33


async def test_search_logs_always_shows_deploys_even_when_filtering_errors(ctx):
    await add_logs(ctx)
    ctx.session.add(
        LogEntry(
            project_id=ctx.project_id,
            ts=T0 + timedelta(minutes=20),
            level="INFO",
            route="-",
            message="deploy started: release v2 (rolling, 5 instances)",
        )
    )
    await ctx.session.flush()
    result = await call(ctx, "search_logs", since=T0.isoformat(), level="ERROR")
    assert result["total"] == 3  # only errors are counted...
    [deploy] = result["deploys"]  # ...but the deploy is still visible
    assert deploy["ts"].startswith("2026-10-06T14:20") and "v2" in deploy["message"]


async def test_a_filter_that_matches_nothing_shows_the_real_values(ctx):
    await add_logs(ctx)
    for entry in (await ctx.session.scalars(select(LogEntry))).all():
        entry.service = "paystream-api"
    await ctx.session.flush()
    result = await call(ctx, "search_logs", service="payment-service")  # a guessed name
    assert result["total"] == 0
    assert result["available"]["services"] == ["paystream-api"]
    assert "/payments" in result["available"]["routes"]
    assert result["available"]["error_types"] == ["OperationalError"]


async def test_the_agent_can_be_told_the_tables_and_columns_up_front(production_data):
    from app.tools.database import describe_tables

    assert await describe_tables(production_data) == "payments(id, http_status)"
    result = await call(production_data, "query_database", sql="SELECT * FROM pg_settings")
    assert "payments(id, http_status)" in result["error"]  # a wrong guess shows the schema
