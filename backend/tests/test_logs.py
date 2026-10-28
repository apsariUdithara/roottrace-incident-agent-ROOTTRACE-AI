"""The logs explorer: GET /projects/{id}/logs and /logs/facets."""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LogEntry
from app.rag.ingest import ingest_logs
from app.tools import ToolContext, call_tool
from tests.support import REAL_LOGS, db, new_project

NewUser = Callable[[], dict[str, str]]
T0 = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)


def line(minute: int, level: str, route: str | None, message: str, **fields) -> dict:
    defaults = {"service": "paystream-api", "status": 200, "error_type": None, "trace_id": None}
    return {
        **defaults,
        "ts": T0 + timedelta(minutes=minute),
        "level": level,
        "route": route,
        "message": message,
        **fields,
    }


# Each message names its line. Added in this order, so the ids follow it.
LINES = [
    line(0, "INFO", "/payments", "paid", trace_id="t1"),
    line(
        1,
        "ERROR",
        "/payments",
        "too many connections",
        status=500,
        error_type="OperationalError",
        trace_id="t2",
    ),
    line(2, "warn", "/orders/total", "slow orders query", service="worker", error_type="SlowQuery"),
    line(
        3,
        "ERROR",
        "/payments/refund",
        "refund failed: pool exhausted",
        status=500,
        error_type="OperationalError",
        trace_id="t2",
    ),
    line(4, "INFO", "-", "deploy started", service="worker", error_type="Deploy"),
    line(4, "INFO", "/health", "healthy", trace_id="t6"),  # same time as the deploy
    line(5, "WARN", "/promo/50%off", "promo code expired", service=""),
]


def add_lines(project_id: str, lines: list[dict]) -> None:
    async def add(session: AsyncSession) -> None:
        session.add_all(LogEntry(project_id=uuid.UUID(project_id), **fields) for fields in lines)

    db(add)


@pytest.fixture
def logs(client: TestClient, new_user: NewUser) -> dict:
    headers = new_user()
    project_id = new_project(client, headers)
    add_lines(project_id, LINES)
    return {"headers": headers, "url": f"/projects/{project_id}/logs"}


def messages(client: TestClient, logs: dict, **query) -> list[str]:
    response = client.get(logs["url"], headers=logs["headers"], params=query)
    assert response.status_code == 200, response.text
    return [item["message"] for item in response.json()["items"]]


def test_lists_lines_newest_first_a_page_at_a_time(client: TestClient, logs: dict):
    first = client.get(logs["url"], headers=logs["headers"], params={"limit": 3}).json()
    rest = client.get(logs["url"], headers=logs["headers"], params={"offset": 3}).json()

    # Lines logged at the same time keep the order they were added in, newest first.
    assert [item["message"] for item in first["items"]] == [
        "promo code expired",
        "healthy",
        "deploy started",
    ]
    assert (first["total"], first["limit"], first["offset"]) == (7, 3, 0)
    assert [item["message"] for item in rest["items"]] == [
        "refund failed: pool exhausted",
        "slow orders query",
        "too many connections",
        "paid",
    ]
    assert first["items"][0] | {"id": 0} == {
        "id": 0,
        "ts": "2026-10-06T14:05:00Z",
        "level": "WARN",
        "service": "",
        "instance": None,
        "route": "/promo/50%off",
        "status": 200,
        "latency_ms": None,
        "message": "promo code expired",
        "error_type": None,
        "trace_id": None,
        "attrs": {},
    }


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # As logged, in any case: "warn" and "WARN" are one level.
        ({"level": "Warn"}, ["promo code expired", "slow orders query"]),
        ({"service": "worker"}, ["deploy started", "slow orders query"]),
        # Part of a route, as in the agent's tool.
        ({"route": "/PAY"}, ["refund failed: pool exhausted", "too many connections", "paid"]),
        (
            {"error_type": "OperationalError"},
            ["refund failed: pool exhausted", "too many connections"],
        ),
        ({"error_type": "operationalerror"}, []),  # exact, as in the agent's tool
        ({"text": "POOL"}, ["refund failed: pool exhausted"]),
        ({"text": "slowquery"}, ["slow orders query"]),  # the error type counts as text
        ({"trace_id": "t2"}, ["refund failed: pool exhausted", "too many connections"]),
        ({"trace_id": "t"}, []),  # exact
        # Both ends are included.
        (
            {"since": "2026-10-06T14:03:00Z", "until": "2026-10-06T14:04:00Z"},
            ["healthy", "deploy started", "refund failed: pool exhausted"],
        ),
        # Without a timezone, UTC.
        ({"since": "2026-10-06T14:04:00"}, ["promo code expired", "healthy", "deploy started"]),
        (
            {"since": "2026-10-06T19:34:00+05:30"},
            ["promo code expired", "healthy", "deploy started"],
        ),
        # The filters combine.
        (
            {"route": "/payments", "level": "ERROR", "until": "2026-10-06T14:02:00Z"},
            ["too many connections"],
        ),
        # % and _ are plain characters, not wildcards.
        ({"route": "%"}, ["promo code expired"]),
        ({"text": "_"}, []),
        # An empty filter is no filter.
        ({"level": "", "route": "", "text": ""}, [entry["message"] for entry in reversed(LINES)]),
    ],
)
def test_filters(client: TestClient, logs: dict, query: dict, expected: list[str]):
    assert messages(client, logs, **query) == expected


def test_total_counts_every_match_not_just_the_page(client: TestClient, logs: dict):
    page = client.get(logs["url"], headers=logs["headers"], params={"level": "error", "limit": 1})

    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1


@pytest.mark.parametrize(
    "query",
    [
        {"since": "2026-10-06T15:00:00Z", "until": "2026-10-06T14:00:00Z"},
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
        {"since": "yesterday"},
    ],
)
def test_rejects_bad_queries(client: TestClient, logs: dict, query: dict):
    response = client.get(logs["url"], headers=logs["headers"], params=query)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"


def test_says_why_a_backwards_window_is_rejected(client: TestClient, logs: dict):
    query = {"since": "2026-10-06T15:00:00Z", "until": "2026-10-06T14:00:00Z"}

    response = client.get(logs["url"], headers=logs["headers"], params=query)

    [error] = response.json()["errors"]
    assert error == {"field": "query", "message": "Value error, since must not be after until"}


def test_shows_only_this_projects_lines(client: TestClient, logs: dict, new_user: NewUser):
    other = new_project(client, logs["headers"], "Other")
    add_lines(other, [line(9, "ERROR", "/payments", "someone else's error")])
    stranger = new_user()

    assert "someone else's error" not in messages(client, logs)
    for url in (logs["url"], logs["url"] + "/facets"):
        assert client.get(url, headers=stranger).status_code == 404
        assert client.get(url).status_code == 401


def test_facets_list_the_values_to_filter_by(client: TestClient, logs: dict):
    facets = client.get(logs["url"] + "/facets", headers=logs["headers"]).json()

    def values(field: str) -> list[tuple[str, int]]:
        return [(item["value"], item["count"]) for item in facets[field]]

    assert (facets["total"], facets["first_ts"], facets["last_ts"]) == (
        7,
        "2026-10-06T14:00:00Z",
        "2026-10-06T14:05:00Z",
    )
    # Levels ignore case, like their filter. Missing and empty values aren't listed.
    assert values("levels") == [("ERROR", 2), ("INFO", 3), ("WARN", 2)]
    assert values("services") == [("paystream-api", 4), ("worker", 2)]
    assert values("routes") == [
        ("-", 1),
        ("/health", 1),
        ("/orders/total", 1),
        ("/payments", 2),
        ("/payments/refund", 1),
        ("/promo/50%off", 1),
    ]
    assert values("error_types") == [("Deploy", 1), ("OperationalError", 2), ("SlowQuery", 1)]


def test_facets_of_a_project_without_logs(client: TestClient, new_user: NewUser):
    headers = new_user()
    project_id = new_project(client, headers)

    facets = client.get(f"/projects/{project_id}/logs/facets", headers=headers).json()

    assert facets == {
        "total": 0,
        "first_ts": None,
        "last_ts": None,
        "levels": [],
        "services": [],
        "routes": [],
        "error_types": [],
    }


# The agent's search_logs calls in scenario 1, and a few more, over the real 6 Oct logs.
SEARCHES = [
    {"since": "2026-10-06T13:00:00Z", "level": "ERROR"},
    {"route": "/payments", "since": "2026-10-06T14:20:00Z", "until": "2026-10-06T14:26:26Z"},
    {"text": "Too Many Connections"},
    {"route": "pay", "error_type": "OperationalError", "service": "paystream-api"},
    {"level": "warn", "until": "2026-10-06T14:00:00Z"},
]


@pytest.fixture
def real_logs(client: TestClient, new_user: NewUser) -> dict:
    headers = new_user()
    project_id = uuid.UUID(new_project(client, headers, "PayStream"))
    rows = [json.loads(row) for row in REAL_LOGS.read_text(encoding="utf-8").splitlines()]
    db(lambda session: ingest_logs(session, project_id, rows))
    return {"headers": headers, "project_id": project_id, "url": f"/projects/{project_id}/logs"}


@pytest.mark.parametrize("search", SEARCHES)
def test_finds_the_same_lines_as_the_agents_search_logs(
    client: TestClient, real_logs: dict, search: dict
):
    async def agent(session: AsyncSession) -> dict:
        context = ToolContext(session, None, real_logs["project_id"], Path("."))
        return json.loads(await call_tool(context, "search_logs", search))

    found = db(agent)
    page = client.get(
        real_logs["url"], headers=real_logs["headers"], params=search | {"limit": 500}
    )

    assert found["total"] > 0
    assert page.json()["total"] == found["total"]
    traces = {item["trace_id"] for item in page.json()["items"]}
    assert {sample["ref"].removeprefix("log trace_id=") for sample in found["samples"]} <= traces
