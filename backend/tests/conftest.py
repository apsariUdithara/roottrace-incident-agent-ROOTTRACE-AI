"""Test setup: the app and every test use a throwaway `<db>_test` database, migrated to head.

Your dev data in DATABASE_URL is never touched; the test database is recreated on every run.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models import Project, User

BACKEND_DIR = Path(__file__).resolve().parents[1]
DB_HINT = "Start Postgres first: docker compose -f infra/docker-compose.yml up -d --wait"

# This must run before app.core.db is imported: the app's engine reads DATABASE_URL at import.
DEV_DATABASE_URL = make_url(get_settings().database_url)
TEST_DATABASE_URL = DEV_DATABASE_URL.set(database=f"{DEV_DATABASE_URL.database}_test")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL.render_as_string(hide_password=False)
get_settings.cache_clear()


async def _recreate_test_database() -> None:
    # Connect to the dev database to drop and create the test one (asyncpg wants a plain DSN).
    dsn = DEV_DATABASE_URL.set(drivername="postgresql").render_as_string(hide_password=False)
    conn = await asyncpg.connect(dsn, timeout=5)
    try:
        name = TEST_DATABASE_URL.database
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def alembic_cfg() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


@pytest.fixture(scope="session", autouse=True)
def test_database(alembic_cfg: Config) -> None:
    try:
        asyncio.run(_recreate_test_database())
    except OSError as exc:  # includes connection refused and TimeoutError
        pytest.exit(f"Postgres is not reachable ({exc}). {DB_HINT}", returncode=1)
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back after the test, even if the test commits."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def project(db_session: AsyncSession) -> Project:
    """A user and their project, for tests of project-scoped tables."""
    owner = User(email="owner@example.com", password_hash="argon2-hash")
    db_session.add(owner)
    await db_session.flush()
    project = Project(owner_id=owner.id, name="Payments")
    db_session.add(project)
    await db_session.flush()
    return project
