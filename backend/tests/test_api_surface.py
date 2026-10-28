"""The API surface from IMPLEMENTATION_PLAN.md §11, plus the routes the backlog adds.

Every route exists with its final request and response models, so the OpenAPI schema and the
TypeScript types generated from it are complete. Routes that aren't built yet answer 501, but only
after the login, ownership and validation checks, so those are already pinned here. The built
investigation routes have their own tests in test_investigations_api.py.
"""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Investigation, InvestigationStatus
from app.schemas.events import EVENT_CLASSES

NewUser = Callable[[], dict[str, str]]

PROJECT = "/projects/{project_id}"
INVESTIGATION = "/investigations/{investigation_id}"

ROUTES = {
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("POST", "/auth/demo"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/me"),
    ("POST", "/chat"),
    ("GET", "/projects"),
    ("POST", "/projects"),
    ("GET", PROJECT),
    ("PATCH", PROJECT),
    ("DELETE", PROJECT),
    ("GET", PROJECT + "/repositories"),
    ("POST", PROJECT + "/repositories"),
    ("PATCH", PROJECT + "/repositories/{repository_id}"),
    ("DELETE", PROJECT + "/repositories/{repository_id}"),
    ("POST", PROJECT + "/ingest"),
    ("POST", PROJECT + "/uploads"),
    ("POST", PROJECT + "/uploads/preview"),
    ("GET", PROJECT + "/knowledge"),
    ("GET", PROJECT + "/sources"),
    ("GET", PROJECT + "/logs"),
    ("GET", PROJECT + "/logs/facets"),
    ("GET", PROJECT + "/incidents"),
    ("POST", PROJECT + "/incidents"),
    ("GET", PROJECT + "/incidents/{incident_id}"),
    ("PATCH", PROJECT + "/incidents/{incident_id}"),
    ("DELETE", PROJECT + "/incidents/{incident_id}"),
    ("GET", PROJECT + "/memories"),
    ("PATCH", PROJECT + "/memories/{memory_id}"),
    ("DELETE", PROJECT + "/memories/{memory_id}"),
    ("GET", PROJECT + "/investigations"),
    ("POST", PROJECT + "/investigations"),
    ("GET", INVESTIGATION),
    ("GET", INVESTIGATION + "/events"),
    ("GET", INVESTIGATION + "/stream"),
    ("GET", INVESTIGATION + "/messages"),
    ("POST", INVESTIGATION + "/messages"),
    ("POST", INVESTIGATION + "/approve"),
    ("POST", INVESTIGATION + "/patches/{patch_id}/rerun"),
    ("GET", INVESTIGATION + "/trace"),
    ("GET", "/evaluation/runs"),
    ("GET", "/evaluation/runs/{run_id}"),
    ("GET", "/stats/dashboard"),
}

# (method, url, backlog task, request kwargs). In urls and bodies, {project_id} and
# {investigation_id} are the owner's; {other_id} is a random id for rows that don't exist yet.
STUBS: list[tuple[str, str, str, dict[str, Any]]] = [
    ("POST", "/auth/demo", "B-1001", {}),
    ("GET", PROJECT + "/sources?ref=commit:a3f9c21", "B-401", {}),
    ("GET", PROJECT + "/memories?category=project_fact&status=active", "B-703", {}),
    ("PATCH", PROJECT + "/memories/{other_id}", "B-703", {"json": {"status": "archived"}}),
    ("DELETE", PROJECT + "/memories/{other_id}", "B-703", {}),
    ("GET", INVESTIGATION + "/messages", "B-701", {}),
    ("POST", INVESTIGATION + "/patches/{other_id}/rerun", "B-604", {}),
    ("GET", INVESTIGATION + "/trace", "B-803", {}),
    ("GET", "/evaluation/runs", "B-901", {}),
    ("GET", "/evaluation/runs/{other_id}", "B-901", {}),
    ("GET", "/stats/dashboard", "B-804", {}),
]
PUBLIC = {"/auth/demo"}  # the one stub for people who aren't logged in
NEEDS_LOGIN = [stub for stub in STUBS if stub[1] not in PUBLIC]
# Stubs whose path names a project or an investigation, so another user must get a 404.
SCOPED = [stub for stub in STUBS if "{project_id}" in stub[1] or "{investigation_id}" in stub[1]]


def ids_for(stubs: list[tuple[str, str, str, dict[str, Any]]]) -> list[str]:
    return [f"{method} {url}" for method, url, _, _ in stubs]


def fill(value: Any, ids: dict[str, str]) -> Any:
    """Put real ids in place of {project_id}, {investigation_id} and {other_id}."""
    if isinstance(value, str):
        for name, id_ in ids.items():
            value = value.replace("{" + name + "}", id_)
        return value
    if isinstance(value, dict):
        return {key: fill(item, ids) for key, item in value.items()}
    return value


def random_ids() -> dict[str, str]:
    return {name: str(uuid.uuid4()) for name in ("project_id", "investigation_id", "other_id")}


async def add_investigation(project_id: str, user_id: str) -> str:
    """Committed, so the app's own sessions see it. (Starting one through the API would run the
    agent.) Completed, so the demo guard rails don't count it as running: they reserve a
    cost cap for every active investigation today, across all users."""
    from app.core.db import engine as app_engine  # points at the test database

    investigation_id = uuid.uuid4()
    engine = create_async_engine(app_engine.url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            session.add(
                Investigation(
                    id=investigation_id,
                    project_id=uuid.UUID(project_id),
                    user_id=uuid.UUID(user_id),
                    question="Why 500s?",
                    status=InvestigationStatus.COMPLETED,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    return str(investigation_id)


@pytest.fixture
def owner(client: TestClient, new_user: NewUser) -> dict[str, Any]:
    """A user with a project and an investigation in it: their auth headers and the ids."""
    headers = new_user()
    project_id = client.post("/projects", headers=headers, json={"name": "Payments"}).json()["id"]
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    investigation_id = asyncio.run(add_investigation(project_id, user_id))
    ids = {**random_ids(), "project_id": project_id, "investigation_id": investigation_id}
    return {"headers": headers, "ids": ids}


def test_every_planned_route_exists(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]

    assert {(method.upper(), path) for path, ops in paths.items() for method in ops} == ROUTES


@pytest.mark.parametrize(("method", "url", "task", "kwargs"), STUBS, ids=ids_for(STUBS))
def test_stub_answers_501_for_the_owner(
    client: TestClient, owner: dict[str, Any], method: str, url: str, task: str, kwargs: dict
):
    ids = owner["ids"]

    response = client.request(method, fill(url, ids), headers=owner["headers"], **fill(kwargs, ids))

    assert response.status_code == 501, response.text
    assert response.json() == {"detail": f"Not implemented yet ({task})", "code": "not_implemented"}


@pytest.mark.parametrize(("method", "url", "task", "kwargs"), NEEDS_LOGIN, ids=ids_for(NEEDS_LOGIN))
def test_stub_requires_login(client: TestClient, method: str, url: str, task: str, kwargs: dict):
    ids = random_ids()

    response = client.request(method, fill(url, ids), **fill(kwargs, ids))

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


@pytest.mark.parametrize(("method", "url", "task", "kwargs"), SCOPED, ids=ids_for(SCOPED))
def test_stub_hides_other_users_projects_and_investigations(
    client: TestClient,
    owner: dict[str, Any],
    new_user: NewUser,
    method: str,
    url: str,
    task: str,
    kwargs: dict,
):
    ids = owner["ids"]

    response = client.request(method, fill(url, ids), headers=new_user(), **fill(kwargs, ids))

    assert response.status_code == 404, response.text
    assert response.json()["detail"] in ("Project not found", "Investigation not found")


def test_stubs_still_validate_requests(client: TestClient, owner: dict[str, Any]):
    headers, project_id = owner["headers"], owner["ids"]["project_id"]

    response = client.post(
        f"/projects/{project_id}/investigations", headers=headers, json={"question": "why"}
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"


def event_refs(schema: dict) -> set[str]:
    return {ref["$ref"].rsplit("/", 1)[1] for ref in schema["oneOf"]}


EVENT_NAMES = {cls.__name__ for cls in EVENT_CLASSES}


def test_events_and_stream_document_every_event_type(client: TestClient):
    """The event union is in the OpenAPI schema, so the frontend's event types are generated."""
    spec = client.get("/openapi.json").json()
    events = spec["paths"][INVESTIGATION + "/events"]["get"]["responses"]["200"]["content"]
    stream = spec["paths"][INVESTIGATION + "/stream"]["get"]

    assert event_refs(events["application/json"]["schema"]["items"]) == EVENT_NAMES
    item = stream["responses"]["200"]["content"]["text/event-stream"]["itemSchema"]
    data = item["properties"]["data"]["contentSchema"]
    assert data["discriminator"]["propertyName"] == "type"
    assert event_refs(data) == EVENT_NAMES
    assert EVENT_NAMES <= set(spec["components"]["schemas"])  # every $ref resolves
    headers = [param["name"] for param in stream["parameters"] if param["in"] == "header"]
    assert headers == ["last-event-id"]


def test_investigation_responses_are_typed(client: TestClient):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    out = schemas["InvestigationOut"]["properties"]

    assert out["status"] == {"$ref": "#/components/schemas/InvestigationStatus"}
    assert {"$ref": "#/components/schemas/InvestigationReport"} in out["report"]["anyOf"]
    assert out["total_cost_usd"]["type"] == "number"  # a number, not a Decimal string
    assert "deployed_ref" in schemas["InvestigationReport"]["required"]
    assert schemas["MessageOut"]["properties"]["role"] == {
        "$ref": "#/components/schemas/MessageRole"
    }


def test_github_token_is_write_only(client: TestClient):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    for body in ("RepositoryCreate", "RepositoryUpdate"):
        token = schemas[body]["properties"]["token"]["anyOf"][0]
        assert (token["type"], token["format"], token["writeOnly"]) == ("string", "password", True)
    assert "token" not in schemas["RepositoryOut"]["properties"]
    assert "encrypted_token" not in schemas["RepositoryOut"]["properties"]
