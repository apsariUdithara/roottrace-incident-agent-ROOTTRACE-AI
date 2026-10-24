"""Users only ever see their own projects and everything inside them."""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_owned_project, owned_select
from app.models import Incident, Investigation, Project, User


async def add_user_with_project(session: AsyncSession, email: str) -> tuple[User, Project]:
    user = User(email=email, password_hash="argon2-hash")
    session.add(user)
    await session.flush()
    project = Project(owner_id=user.id, name="Payments")
    session.add(project)
    await session.flush()
    return user, project


async def test_owner_gets_their_project(db_session: AsyncSession) -> None:
    alice, project = await add_user_with_project(db_session, "alice@example.com")

    assert await get_owned_project(project.id, alice, db_session) is project


async def test_another_users_project_looks_like_a_missing_one(db_session: AsyncSession) -> None:
    _, alice_project = await add_user_with_project(db_session, "alice@example.com")
    bob, _ = await add_user_with_project(db_session, "bob@example.com")

    with pytest.raises(HTTPException) as someone_elses:
        await get_owned_project(alice_project.id, bob, db_session)
    with pytest.raises(HTTPException) as missing:
        await get_owned_project(uuid.uuid4(), bob, db_session)

    assert someone_elses.value.status_code == missing.value.status_code == 404
    assert someone_elses.value.detail == missing.value.detail == "Project not found"


async def test_owned_select_scopes_projects_and_their_rows(db_session: AsyncSession) -> None:
    alice, alice_project = await add_user_with_project(db_session, "alice@example.com")
    bob, bob_project = await add_user_with_project(db_session, "bob@example.com")
    alice_investigation = Investigation(
        project_id=alice_project.id, user_id=alice.id, question="Why 500s?"
    )
    bob_investigation = Investigation(project_id=bob_project.id, user_id=bob.id, question="Why?")
    alice_incident = Incident(project_id=alice_project.id, key="INC-017", title="Pool exhaustion")
    db_session.add_all([alice_investigation, bob_investigation, alice_incident])
    await db_session.flush()

    async def visible(model: type, user: User) -> list:
        return list(await db_session.scalars(owned_select(model, user)))

    assert await visible(Project, bob) == [bob_project]
    assert await visible(Investigation, alice) == [alice_investigation]
    assert await visible(Investigation, bob) == [bob_investigation]
    assert await visible(Incident, alice) == [alice_incident]
    assert await visible(Incident, bob) == []
