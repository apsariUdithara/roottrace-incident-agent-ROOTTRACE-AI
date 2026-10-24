"""query_database: read-only SQL over the project's production data snapshot.

The SQL comes from an LLM, so it is treated as untrusted. Four independent layers, any one of
which stops an attack:

1. Parse it with sqlglot: exactly one SELECT; tables only from the `demo_app` schema; no
   functions sqlglot doesn't recognise (that blocks pg_read_file, dblink, etc.).
2. Run it in a READ ONLY transaction: Postgres itself refuses any write.
3. statement_timeout: a runaway query is killed after a few seconds.
4. LIMIT is forced from the outside, and everything is rolled back afterwards.

In production a dedicated SELECT-only role (READONLY_DATABASE_URL) adds a fifth layer.
"""

import logging

import sqlglot
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlglot import exp

from app.tools.registry import ToolContext, ToolError, tool

# The parser warns on SQL it can't read; the guard below rejects it anyway, so stay quiet.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

SCHEMA = "demo_app"
MAX_ROWS = 100
TIMEOUT_MS = 3000


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

    savepoint = await ctx.session.begin_nested()
    try:
        await ctx.session.execute(text("SET TRANSACTION READ ONLY"))
        await ctx.session.execute(text(f"SET LOCAL statement_timeout = {TIMEOUT_MS}"))
        await ctx.session.execute(text(f"SET LOCAL search_path TO {SCHEMA}"))
        result = await ctx.session.execute(
            text(f"SELECT * FROM ({safe_sql}) AS q LIMIT {MAX_ROWS + 1}")
        )
        columns = list(result.keys())
        rows = [list(r) for r in result.all()]
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"query failed: {str(exc).splitlines()[0]}") from None
    finally:
        await savepoint.rollback()  # undoes the settings too
    return {
        "ref": f"sql: {safe_sql}",
        "columns": columns,
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
    }
