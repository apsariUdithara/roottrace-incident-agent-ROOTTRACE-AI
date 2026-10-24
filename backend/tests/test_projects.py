import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

NewUser = Callable[[], dict[str, str]]


def create(client: TestClient, headers: dict[str, str], name: str = "Payments", **fields):
    return client.post("/projects", headers=headers, json={"name": name, **fields})


def test_create_then_list_and_get(client: TestClient, new_user: NewUser):
    alice = new_user()

    created = create(client, alice, "  Payments  ", description="Checkout and refunds")

    assert created.status_code == 201
    project = created.json()
    assert (project["name"], project["description"]) == ("Payments", "Checkout and refunds")
    assert client.get("/projects", headers=alice).json() == [project]
    assert client.get(f"/projects/{project['id']}", headers=alice).json() == project


def test_list_is_newest_first_and_only_your_own(client: TestClient, new_user: NewUser):
    alice, bob = new_user(), new_user()
    create(client, alice, "First")
    create(client, alice, "Second")
    create(client, bob, "Bob's")

    names = [p["name"] for p in client.get("/projects", headers=alice).json()]
    assert names == ["Second", "First"]


def test_another_users_project_is_invisible(client: TestClient, new_user: NewUser):
    """User 2 can't read, change or delete user 1's project; it looks like it doesn't exist."""
    alice, bob = new_user(), new_user()
    project_id = create(client, alice).json()["id"]
    missing = client.get(f"/projects/{uuid.uuid4()}", headers=bob)

    for response in (
        client.get(f"/projects/{project_id}", headers=bob),
        client.patch(f"/projects/{project_id}", headers=bob, json={"name": "Hijacked"}),
        client.delete(f"/projects/{project_id}", headers=bob),
    ):
        assert response.status_code == 404
        assert (
            response.json()
            == missing.json()
            == {"detail": "Project not found", "code": "not_found"}
        )

    assert client.get(f"/projects/{project_id}", headers=alice).json()["name"] == "Payments"


def test_names_are_unique_per_owner(client: TestClient, new_user: NewUser):
    alice, bob = new_user(), new_user()
    create(client, alice, "Payments")

    duplicate = create(client, alice, "Payments")
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "detail": "You already have a project with this name",
        "code": "conflict",
    }
    assert create(client, bob, "Payments").status_code == 201


def test_update_changes_only_the_fields_sent(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = create(client, alice, "Payments", description="Old").json()["id"]
    create(client, alice, "Billing")

    renamed = client.patch(f"/projects/{project_id}", headers=alice, json={"name": "Checkout"})
    assert renamed.status_code == 200
    assert (renamed.json()["name"], renamed.json()["description"]) == ("Checkout", "Old")

    clash = client.patch(f"/projects/{project_id}", headers=alice, json={"name": "Billing"})
    assert clash.status_code == 409


def test_delete(client: TestClient, new_user: NewUser):
    alice = new_user()
    project_id = create(client, alice).json()["id"]

    assert client.delete(f"/projects/{project_id}", headers=alice).status_code == 204
    assert client.get(f"/projects/{project_id}", headers=alice).status_code == 404
    assert client.get("/projects", headers=alice).json() == []


@pytest.mark.parametrize(
    "body",
    [{"name": ""}, {"name": "   "}, {"name": "x" * 201}, {}],
    ids=["empty", "whitespace", "too-long", "missing"],
)
def test_create_validates_the_name(client: TestClient, new_user: NewUser, body: dict):
    assert client.post("/projects", headers=new_user(), json=body).status_code == 422


def test_requires_login(client: TestClient):
    assert client.get("/projects").status_code == 401
    assert client.post("/projects", json={"name": "Payments"}).status_code == 401


def test_malformed_project_id_is_rejected(client: TestClient, new_user: NewUser):
    assert client.get("/projects/not-a-uuid", headers=new_user()).status_code == 422
