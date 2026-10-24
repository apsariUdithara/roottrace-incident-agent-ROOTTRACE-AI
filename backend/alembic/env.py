"""Alembic environment: migrates the database in DATABASE_URL to match app.models."""

import asyncio
from logging.config import fileConfig

from alembic import context
from alembic.autogenerate.api import AutogenContext
from pgvector.sqlalchemy import Vector
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models import Base

config = context.config

# Keep loggers that already exist (e.g. when tests run migrations in-process).
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def render_item(type_: str, obj: object, autogen_context: AutogenContext) -> str | bool:
    """Write pgvector columns as `Vector(1024)` with an import, instead of an unimported path."""
    if type_ == "type" and isinstance(obj, Vector):
        autogen_context.imports.add("from pgvector.sqlalchemy import Vector")
        return f"Vector({obj.dim})"
    return False  # default rendering


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """Only compare tables we define. Tables created elsewhere (LangGraph's checkpoint
    tables, the demo_app schema) are left alone instead of showing up as "removed"."""
    if type_ == "table":
        return name in target_metadata.tables
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade head --sql`)."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        include_name=include_name,
        render_item=render_item,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Teach reflection the pgvector type, so autogenerate compares embedding columns correctly.
    connection.dialect.ischema_names["vector"] = Vector
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
