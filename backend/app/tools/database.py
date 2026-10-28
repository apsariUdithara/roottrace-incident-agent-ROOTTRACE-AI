"""query_database: read-only SQL over the project's production data snapshot.

The SQL comes from an LLM, so it is treated as untrusted. Four independent layers, any one of
which stops an attack:

1. Parse it with sqlglot: exactly one SELECT; tables only from the `demo_app` schema; no
   functions sqlglot doesn't recognise (that blocks pg_read_file, dblink, etc.).
2. Run it in a READ ONLY transaction: Postgres itself refuses any write.
3. statement_timeout: a runaway query is killed after a few seconds.
4. LIMIT is forced from the outside, and everything is rolled back afterwards.

5. With READONLY_DATABASE_URL set (as in production), the query runs as that role, which may
   only SELECT from demo_app: even a query that got past all the above can't read app tables
   such as users. grant_readonly_access() sets the role up; the demo seed runs it.
"""

import logging
import re
from functools import lru_cache

import sqlglot
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlglot import exp

from app.core.config import get_settings
from app.tools.registry import ToolContext, ToolError, tool

# The parser warns on SQL it can't read; the guard below rejects it anyway, so stay quiet.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

SCHEMA = "demo_app"
MAX_ROWS = 100
TIMEOUT_MS = 3000
ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


async def grant_readonly_access(session: AsyncSession, url: str) -> str:
    """Create (or update) the role in READONLY_DATABASE_URL, with its password, and let it
    SELECT from demo_app and nothing else. Returns the role's name.

    Run it after demo_app is (re)built: dropping the schema drops its grants. Needs a user that
    may create roles, like the compose file's database owner.
    """
    parsed = make_url(url)
    role, password = parsed.username or "", parsed.password or ""
    if not ROLE_NAME.match(role) or not password:
        raise ValueError("READONLY_DATABASE_URL needs a lowercase role name and a password")
    exists = await session.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
    statements = [
        f"{'ALTER' if exists else 'CREATE'} ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB "
        "NOCREATEROLE NOINHERIT PASSWORD %L",
        "ALTER ROLE %I SET default_transaction_read_only = on",
        f"ALTER ROLE %I SET statement_timeout = {TIMEOUT_MS}",
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO %I",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO %I",
    ]
    for statement in statements:
        # format() quotes the name (%I) and the password (%L): DDL takes no bind parameters.
        ddl = await session.scalar(
            text("SELECT format(:statement, CAST(:role AS text), CAST(:password AS text))"),
            {"statement": statement, "role": role, "password": password},
        )
        await session.execute(text(ddl))
    return role


@lru_cache
def _readonly_engine(url: str) -> AsyncEngine:
    # No pool: the agent runs a few queries per investigation, and pooled connections would be
    # tied to the event loop that opened them.
    return create_async_engine(url, poolclass=NullPool)


def readonly_engine() -> AsyncEngine | None:
    url = get_settings().readonly_database_url
    return _readonly_engine(url) if url else None


class QueryArgs(BaseModel):
    sql: str = Field(
        max_length=2000,
        description=f"One SELECT statement over the {SCHEMA} tables (no schema prefix needed). "
        "Use it for counts and time ranges in production data, e.g. failed payments per hour.",
    )


async def allowed_tables(ctx: ToolContext) -> set[str]:
    rows = await ctx.session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
        {"s": SCHEMA},
    )
    return set(rows.scalars())


async def describe_tables(ctx: ToolContext) -> str:
    """The tables query_database can read, with their columns, e.g. "payments(id, created_at)".

    Given to the agent up front, so it never spends tool calls discovering the schema.
    """
    rows = await ctx.session.execute(
        text(
            "SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position) "
            "FROM information_schema.columns WHERE table_schema = :s GROUP BY table_name "
            "ORDER BY table_name"
        ),
        {"s": SCHEMA},
    )
    return "; ".join(f"{table}({columns})" for table, columns in rows.all())


def check_sql(sql: str, tables: set[str]) -> str:
    """Return the SQL if it passes every static check, else raise ToolError."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise ToolError(f"could not parse SQL: {exc}") from None
    if len(statements) != 1:
        raise ToolError("send exactly one statement")
    tree = statements[0]
    if not isinstance(tree, exp.Select) or tree.args.get("into"):
        raise ToolError("only SELECT queries are allowed")
    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.name in ctes:
            continue
        if table.db or table.catalog or table.name not in tables:
            available = ", ".join(sorted(tables))
            raise ToolError(f"unknown table {table.sql()!r}. Available: {available}")
    if fn := next(tree.find_all(exp.Anonymous), None):
        raise ToolError(f"function {fn.name}() is not allowed")
    return tree.sql(dialect="postgres")


async def run_read_only(executor, safe_sql: str) -> tuple[list[str], list[list]]:
    """Run checked SQL in a read-only transaction, with a timeout and a row limit. `executor` is
    a session or a connection, inside a transaction its caller rolls back."""
    await executor.execute(text("SET TRANSACTION READ ONLY"))
    await executor.execute(text(f"SET LOCAL statement_timeout = {TIMEOUT_MS}"))
    await executor.execute(text(f"SET LOCAL search_path TO {SCHEMA}"))
    result = await executor.execute(text(f"SELECT * FROM ({safe_sql}) AS q LIMIT {MAX_ROWS + 1}"))
    return list(result.keys()), [list(r) for r in result.all()]


@tool(
    "query_database",
    f"Run a read-only SELECT on production data (schema {SCHEMA}). At most {MAX_ROWS} rows are "
    "returned. The tables and their columns are listed in your instructions; database system "
    "tables (pg_settings, information_schema) are not available.",
    QueryArgs,
)
async def query_database(ctx: ToolContext, args: QueryArgs) -> dict:
    tables = await allowed_tables(ctx)
    if not tables:
        raise ToolError("this project has no production data snapshot to query")
    try:
        safe_sql = check_sql(args.sql, tables)
    except ToolError as exc:
        raise ToolError(f"{exc}. Tables and columns: {await describe_tables(ctx)}") from None

    try:
        if engine := readonly_engine():
            async with engine.connect() as conn:  # never committed: rolled back on close
                columns, rows = await run_read_only(conn, safe_sql)
        else:
            savepoint = await ctx.session.begin_nested()
            try:
                columns, rows = await run_read_only(ctx.session, safe_sql)
            finally:
                await savepoint.rollback()  # undoes the settings too
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"query failed: {str(exc).splitlines()[0]}") from None
    return {
        "ref": f"sql: {safe_sql}",
        "columns": columns,
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
    }
