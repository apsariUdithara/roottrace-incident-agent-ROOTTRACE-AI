import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import REFRESH_COOKIE
from app.core.security import create_access_token, create_refresh_token
from app.models import User

PASSWORD = "correct horse battery"


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


def register(client: TestClient, email: str | None = None, password: str = PASSWORD):
    return client.post("/auth/register", json={"email": email or new_email(), "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_returns_an_access_token_and_sets_the_refresh_cookie(client: TestClient):
    response = register(client, "Alice.Smith@Example.com")

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "alice.smith@example.com"
    assert (body["token_type"], body["expires_in"]) == ("bearer", 15 * 60)
    assert PASSWORD not in response.text
    cookie = response.headers["set-cookie"].lower()
    assert f"{REFRESH_COOKIE}=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "secure" not in cookie  # HTTPS-only is production's job


async def test_password_is_stored_as_an_argon2_hash(
    client: TestClient, db_session: AsyncSession
) -> None:
    email = new_email()
    register(client, email)

    stored = await db_session.scalar(select(User.password_hash).where(User.email == email))
    assert stored.startswith("$argon2id$")


def test_emails_are_unique_ignoring_case(client: TestClient):
    email = new_email()
    register(client, email)

    response = register(client, email.upper())
    assert response.status_code == 409
    assert response.json()["detail"] == "An account with this email already exists"


@pytest.mark.parametrize(
    ("email", "password"),
    [("not-an-email", PASSWORD), ("ok@example.com", "short"), ("ok@example.com", "x" * 129)],
    ids=["bad-email", "short-password", "huge-password"],
)
def test_register_validates_input(client: TestClient, email: str, password: str):
    assert register(client, email, password).status_code == 422


def test_login_then_me(client: TestClient):
    email = new_email()
    register(client, email)

    response = client.post("/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert response.status_code == 200
    assert REFRESH_COOKIE in response.cookies

    me = client.get("/auth/me", headers=bearer(response.json()["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_wrong_password_and_unknown_email_look_the_same(client: TestClient):
    email = new_email()
    register(client, email)

    wrong_password = client.post("/auth/login", json={"email": email, "password": "nope-nope"})
    unknown_email = client.post("/auth/login", json={"email": new_email(), "password": PASSWORD})

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert (
        wrong_password.json()
        == unknown_email.json()
        == {"detail": "Incorrect email or password", "code": "unauthorized"}
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        bearer("not-a-jwt"),
        bearer(create_refresh_token(uuid.uuid4())),  # right signature, wrong token type
        bearer(create_access_token(uuid.uuid4())),  # valid token, but no such user
    ],
    ids=["missing", "garbage", "refresh-token", "unknown-user"],
)
def test_me_requires_a_valid_access_token(client: TestClient, headers: dict[str, str]):
    response = client.get("/auth/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_refresh_issues_a_new_access_token_and_rotates_the_cookie(client: TestClient):
    register(client)
    first_cookie = client.cookies[REFRESH_COOKIE]

    response = client.post("/auth/refresh")

    assert response.status_code == 200
    assert client.cookies[REFRESH_COOKIE] != first_cookie
    me = client.get("/auth/me", headers=bearer(response.json()["access_token"]))
    assert me.status_code == 200


def test_refresh_needs_a_refresh_token_cookie(client: TestClient):
    assert client.post("/auth/refresh").status_code == 401

    client.cookies.set(REFRESH_COOKIE, create_access_token(uuid.uuid4()))  # wrong token type
    assert client.post("/auth/refresh").status_code == 401


def test_logout_clears_the_refresh_cookie(client: TestClient):
    register(client)

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert REFRESH_COOKIE not in client.cookies
    assert client.post("/auth/refresh").status_code == 401
