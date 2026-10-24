"""Async SQLAlchemy engine and the per-request session dependency."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

engine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,  # drop dead connections (e.g. after a Postgres restart) before use
    connect_args={"timeout": 5},  # asyncpg connect timeout, seconds
)

# expire_on_commit=False keeps loaded attributes readable after commit without extra async I/O.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request. Routes commit explicitly; anything uncommitted is rolled back."""
    async with SessionLocal() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
