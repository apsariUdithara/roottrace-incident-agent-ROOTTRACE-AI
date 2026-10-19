"""Every error has the same JSON shape, and CORS only lets the frontend in."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.errors import install_error_handlers

FRONTEND = get_settings().frontend_origin


def test_unknown_route_and_wrong_method(client: TestClient):
    assert client.get("/no-such-route").json() == {"detail": "Not Found", "code": "not_found"}

    wrong_method = client.put("/projects")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["code"] == "method_not_allowed"


def test_http_errors_keep_their_headers(client: TestClient):
    response = client.get("/auth/me")

    assert response.json() == {"detail": "Not authenticated", "code": "unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_validation_errors_list_fields_without_echoing_input(client: TestClient):
    secret = "my-secret-password-" * 10  # 190 chars: over the 128 limit
    response = client.post("/auth/register", json={"email": "not-an-email", "password": secret})

    assert response.status_code == 422
    body = response.json()
    assert (body["detail"], body["code"]) == ("Request validation failed", "validation_error")
    assert {error["field"] for error in body["errors"]} == {"body.email", "body.password"}
    assert secret not in response.text and "not-an-email" not in response.text


def test_unhandled_errors_become_a_generic_500():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password is hunter2")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "code": "internal_error"}
    assert "hunter2" not in response.text


def test_cors_allows_the_frontend(client: TestClient):
    preflight = client.options(
        "/projects",
        headers={
            "Origin": FRONTEND,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == FRONTEND
    assert preflight.headers["access-control-allow-credentials"] == "true"


def test_cors_rejects_other_sites(client: TestClient):
    evil = "https://evil.example"
    preflight = client.options(
        "/projects", headers={"Origin": evil, "Access-Control-Request-Method": "POST"}
    )
    simple = client.get("/health", headers={"Origin": evil})

    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers
    assert "access-control-allow-origin" not in simple.headers
