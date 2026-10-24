import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Repository, SandboxStatus, User


async def add_user(session: AsyncSession, email: str = "oncall@example.com") -> User:
    user = User(email=email, password_hash="argon2-hash")
    session.add(user)
    await session.flush()
    return user


async def add_project(session: AsyncSession, owner: User, name: str = "Payments") -> Project:
    project = Project(owner_id=owner.id, name=name)
    session.add(project)
    await session.flush()
    return project


async def add_repository(session: AsyncSession, project: Project) -> Repository:
    repo = Repository(project_id=project.id, url="https://github.com/acme/payments")
    session.add(repo)
    await session.flush()
    return repo


async def add_user_project_repository(session: AsyncSession) -> Repository:
    return await add_repository(session, await add_project(session, await add_user(session)))


async def test_repository_defaults(db_session: AsyncSession) -> None:
    repo = await add_user_project_repository(db_session)
    await db_session.refresh(repo)

    assert isinstance(repo.id, uuid.UUID)
    assert repo.created_at is not None
    assert repo.provider == "github"
    assert repo.default_branch == "main"
    assert repo.setup_command is None  # auto-detect
    assert repo.test_command == "pytest -q"
    assert repo.sandbox_status is SandboxStatus.UNKNOWN


async def test_raw_sql_inserts_get_server_defaults(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "INSERT INTO users (email, password_hash) VALUES ('seed@example.com', 'x') "
                "RETURNING id, created_at"
            )
        )
    ).one()

    assert isinstance(row.id, uuid.UUID)
    assert row.created_at.tzinfo is not None


async def test_email_is_unique(db_session: AsyncSession) -> None:
    await add_user(db_session, "dup@example.com")
    db_session.add(User(email="dup@example.com", password_hash="x"))

    with pytest.raises(IntegrityError, match="uq_users_email"):
        await db_session.flush()


async def test_project_names_are_unique_per_owner(db_session: AsyncSession) -> None:
    alice = await add_user(db_session, "alice@example.com")
    bob = await add_user(db_session, "bob@example.com")
    await add_project(db_session, alice, "Payments")
    await add_project(db_session, bob, "Payments")  # another owner may reuse the name

    with pytest.raises(IntegrityError, match="uq_projects_owner_id_name"):
        await add_project(db_session, alice, "Payments")


async def test_sandbox_status_rejects_unknown_values(db_session: AsyncSession) -> None:
    repo = await add_user_project_repository(db_session)

    with pytest.raises(IntegrityError, match="ck_repositories_sandbox_status"):
        await db_session.execute(
            text("UPDATE repositories SET sandbox_status = 'broken' WHERE id = :id"),
            {"id": repo.id},
        )


async def test_deleting_a_user_removes_their_projects_and_repositories(
    db_session: AsyncSession,
) -> None:
    user = await add_user(db_session)
    project = await add_project(db_session, user)
    await add_repository(db_session, project)

    await db_session.delete(user)
    await db_session.flush()

    projects = await db_session.scalar(
        select(func.count()).select_from(Project).where(Project.owner_id == user.id)
    )
    repositories = await db_session.scalar(
        select(func.count()).select_from(Repository).where(Repository.project_id == project.id)
    )
    assert (projects, repositories) == (0, 0)


async def test_pgvector_is_enabled(db_session: AsyncSession) -> None:
    version = await db_session.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    assert version is not None
