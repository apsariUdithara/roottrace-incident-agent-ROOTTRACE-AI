"""The SELECT-only role query_database runs as in production (READONLY_DATABASE_URL).

These use real roles on the test server. Roles are shared by every database on a server, so each
test makes a fresh one and drops it afterwards.
"""

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.tools import database
from app.tools.database import QueryArgs, grant_readonly_access, query_database
from app.tools.registry import ToolContext, ToolError

PASSWORD = "it's a 'test' %pw"  # quotes and % must survive the DDL


def app_url() -> URL:
    from app.core.db import engine  # points at the test database

    return engine.url


@pytest.fixture
async def admin() -> AsyncIterator[AsyncEngine]:
    """The app's own login, which owns demo_app (as in the compose file)."""
    engine = create_async_engine(app_url(), poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def readonly_url(admin: AsyncEngine) -> AsyncIterator[str]:
    """A committed demo_app with three payments, and a fresh read-only role for it."""
    role = f"roottrace_readonly_{uuid.uuid4().hex[:8]}"
    url = app_url().set(username=role, password=PASSWORD).render_as_string(hide_password=False)
    async with admin.begin() as conn:
        await conn.execute(text("CREATE SCHEMA demo_app"))
        await conn.execute(text("CREATE TABLE demo_app.payments (id int, http_status int)"))
        await conn.execute(
            text("INSERT INTO demo_app.payments VALUES (1, 200), (2, 500), (3, 500)")
        )
    async with AsyncSession(admin) as session:
        assert await grant_readonly_access(session, url) == role
        await session.commit()
    yield url
    async with admin.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS demo_app CASCADE"))
        await conn.execute(text(f"DROP OWNED BY {role}"))
        await conn.execute(text(f"DROP ROLE {role}"))


async def as_role(url: str, sql: str):
    """Run one statement as the role in `url`, in its own connection."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.scalar(text(sql))
    finally:
        await engine.dispose()


async def test_the_role_reads_demo_app_and_nothing_else(readonly_url: str):
    assert await as_role(readonly_url, "SELECT count(*) FROM demo_app.payments") == 3
    assert await as_role(readonly_url, "SHOW default_transaction_read_only") == "on"

    for sql in (
        "SELECT password_hash FROM users",  # app tables
        "SELECT count(*) FROM investigations",
        "INSERT INTO demo_app.payments VALUES (4, 200)",  # no writes, even in demo_app
        "CREATE TABLE demo_app.stolen (x int)",
        "CREATE TABLE public.stolen (x int)",
    ):
        with pytest.raises(DBAPIError):
            await as_role(readonly_url, sql)


async def test_granting_again_after_a_rebuild_restores_access(
    readonly_url: str, admin: AsyncEngine
):
    async with admin.begin() as conn:  # what the seed does on every run
        await conn.execute(text("DROP SCHEMA demo_app CASCADE"))
        await conn.execute(text("CREATE SCHEMA demo_app"))
        await conn.execute(text("CREATE TABLE demo_app.payments AS SELECT 1 AS id"))
    with pytest.raises(DBAPIError):  # dropping the schema dropped the grants
        await as_role(readonly_url, "SELECT count(*) FROM demo_app.payments")

    async with AsyncSession(admin) as session:
        await grant_readonly_access(session, readonly_url)  # the role exists: updated, not made
        await session.commit()

    assert await as_role(readonly_url, "SELECT count(*) FROM demo_app.payments") == 1


async def test_query_database_runs_as_the_readonly_role(
    readonly_url: str, admin: AsyncEngine, monkeypatch: pytest.MonkeyPatch
):
    settings = get_settings().model_copy(update={"readonly_database_url": readonly_url})
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    count = QueryArgs(sql="SELECT count(*) AS n FROM payments")

    async with AsyncSession(admin) as session:
        tools = ToolContext(session, None, uuid.uuid4(), Path("."))
        assert (await query_database(tools, count))["rows"] == [[3]]

        role = make_url(readonly_url).username
        async with admin.begin() as conn:
            await conn.execute(text(f"REVOKE SELECT ON ALL TABLES IN SCHEMA demo_app FROM {role}"))

        # The app's own login could still read it: the failure shows the query ran as the role.
        with pytest.raises(ToolError, match="permission denied"):
            await query_database(tools, count)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://Robert'); DROP TABLE users;--:pw@localhost/roottrace",
        "postgresql+asyncpg://roottrace_readonly@localhost/roottrace",  # no password
    ],
    ids=["unsafe role name", "no password"],
)
async def test_refuses_a_url_it_cant_use_safely(url: str):
    with pytest.raises(ValueError, match="lowercase role name and a password"):
        await grant_readonly_access(None, url)
