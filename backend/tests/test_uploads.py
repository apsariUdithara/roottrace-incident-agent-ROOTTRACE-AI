"""Uploads: POST /projects/{id}/uploads/preview and /uploads, for logs and incident history."""

import json
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Incident, LogEntry
from app.rag import uploads
from app.tools.registry import ToolContext, call_tool
from app.tools.search import SearchArgs, search_previous_incidents
from tests.support import REAL_INCIDENTS, REAL_LOGS, FakeEmbedder, db, new_project

NewUser = Callable[[], dict[str, str]]

ACCESS_LOG = """timestamp,severity,host,path,status_code,duration_ms,msg,trace,region
2026-10-06T14:26:27Z,ERROR,api-3,/payments,500,5003,too many connections,5a07979f,eu-1
2026-10-06T14:26:20Z,INFO,api-1,/payments,200,88,request completed,4ecb5ffc,eu-1
2026-10-06T14:26:21Z,WARN,api-2,/webhooks/gateway,400,17,missing field,6c0fd4f5,us-2
"""


@pytest.fixture
def alice(client: TestClient, new_user: NewUser) -> dict:
    headers = new_user()
    project_id = new_project(client, headers, "Payments")
    return {"headers": headers, "project_id": project_id, "url": f"/projects/{project_id}"}


def send(
    client: TestClient,
    alice: dict,
    kind: str,
    filename: str,
    content: str | bytes,
    mapping: dict | None = None,
    *,
    preview: bool = False,
):
    data = {"kind": kind}
    if mapping is not None:
        data["mapping"] = json.dumps(mapping)
    raw = content.encode() if isinstance(content, str) else content
    return client.post(
        f"{alice['url']}/uploads" + ("/preview" if preview else ""),
        headers=alice["headers"],
        data=data,
        files={"file": (filename, raw, "application/octet-stream")},
    )


def imported(response) -> dict:
    assert response.status_code == 201, response.text
    return response.json()


def lines(client: TestClient, alice: dict, **query) -> list[dict]:
    page = client.get(
        f"{alice['url']}/logs", headers=alice["headers"], params=query | {"limit": 500}
    )
    return page.json()["items"]


def count(model, project_id: str) -> int:
    async def run(session: AsyncSession) -> int:
        query = select(func.count()).select_from(model)
        return await session.scalar(query.where(model.project_id == uuid.UUID(project_id)))

    return db(run)


# --- Logs ---------------------------------------------------------------------------------------


def test_preview_guesses_the_mapping_and_saves_nothing(client: TestClient, alice: dict):
    preview = send(client, alice, "logs", "access.csv", ACCESS_LOG, preview=True)

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["format"], body["rows"], body["filename"]) == ("csv", 3, "access.csv")
    assert body["columns"][:3] == ["timestamp", "severity", "host"]
    assert body["mapping"] == {
        "ts": "timestamp",
        "level": "severity",
        "instance": "host",
        "route": "path",
        "status": "status_code",
        "latency_ms": "duration_ms",
        "message": "msg",
        "trace_id": "trace",
    }  # region is kept with each line instead
    assert body["sample"][0]["path"] == "/payments"
    assert [(f["name"], f["required"]) for f in body["fields"][:3]] == [
        ("ts", True),
        ("level", True),
        ("service", False),
    ]
    assert body["mapping_error"] is None
    assert body["result"] | {"errors": None} == {
        "kind": "logs",
        "filename": "access.csv",
        "imported": 3,
        "skipped": 0,
        "failed": 0,
        "errors": None,
    }
    assert count(LogEntry, alice["project_id"]) == 0


def test_uploaded_logs_are_what_the_agent_searches(client: TestClient, alice: dict):
    result = imported(send(client, alice, "logs", "access.csv", ACCESS_LOG))

    assert (result["imported"], result["skipped"], result["failed"]) == (3, 0, 0)
    [error] = lines(client, alice, level="error")
    assert error | {"id": 0} == {
        "id": 0,
        "ts": "2026-10-06T14:26:27Z",
        "level": "ERROR",
        "service": None,
        "instance": "api-3",
        "route": "/payments",
        "status": 500,
        "latency_ms": 5003,
        "message": "too many connections",
        "error_type": None,
        "trace_id": "5a07979f",
        "attrs": {"region": "eu-1"},  # the column that isn't one of ours
    }

    async def agent(session: AsyncSession) -> dict:
        context = ToolContext(session, None, uuid.UUID(alice["project_id"]), Path("."))
        return json.loads(await call_tool(context, "search_logs", {"route": "/payments"}))

    assert db(agent)["total"] == 2


def test_uploading_a_file_twice_imports_it_once(client: TestClient, alice: dict):
    twice = ACCESS_LOG + ACCESS_LOG.splitlines(keepends=True)[1]  # one line logged twice

    first = imported(send(client, alice, "logs", "access.csv", twice))
    again = imported(send(client, alice, "logs", "access.csv", twice))

    assert (first["imported"], first["skipped"]) == (4, 0)  # both copies: they're in the file
    assert (again["imported"], again["skipped"]) == (0, 4)
    assert len(lines(client, alice)) == 4


def test_the_demo_log_file_uploads_as_it_is(client: TestClient, alice: dict):
    content = REAL_LOGS.read_bytes()

    preview = send(client, alice, "logs", REAL_LOGS.name, content, preview=True).json()
    result = imported(send(client, alice, "logs", REAL_LOGS.name, content))

    assert preview["format"] == "jsonl"
    assert preview["mapping"] == {name: name for name in [t.name for t in uploads.LOG_FIELDS]}
    assert (result["imported"], result["failed"]) == (1004, 0)
    assert len(lines(client, alice, error_type="OperationalError")) == 94
    assert all(not line["attrs"] for line in lines(client, alice, route="/health"))


def test_rows_with_problems_are_listed_and_left_out(client: TestClient, alice: dict):
    good = {"ts": "2026-10-06T14:00:00Z", "level": "INFO", "message": "ok"}
    rows = [
        json.dumps(good),
        "this isn't json",
        "[1, 2]",
        json.dumps(good | {"ts": "yesterday"}),
        json.dumps({"ts": "2026-10-06T14:00:01Z", "message": "no level"}),
        json.dumps(good | {"status": "five hundred"}),
        "",  # blank lines don't count
        json.dumps(good | {"route": "/" + "x" * 600}),
        json.dumps(good | {"latency_ms": -3}),
        json.dumps(good | {"message": "fine too", "ts": 1791295200}),  # seconds since 1970
    ]

    result = imported(send(client, alice, "logs", "app.jsonl", "\n".join(rows)))

    assert (result["imported"], result["failed"]) == (2, 7)
    assert result["errors"] == [
        {"row": 2, "message": "not valid JSON"},
        {"row": 3, "message": "not a JSON object"},
        {"row": 4, "message": "ts: not a time"},
        {"row": 5, "message": "level: missing"},
        {"row": 6, "message": "status: not a number"},
        {"row": 8, "message": "route: longer than 500 characters"},  # its line in the file
        {"row": 9, "message": "latency_ms: out of range"},
    ]
    assert {line["message"]: line["ts"] for line in lines(client, alice)} == {
        "ok": "2026-10-06T14:00:00Z",
        "fine too": "2026-10-06T14:00:00Z",
    }


def test_at_most_20_problems_are_listed(client: TestClient, alice: dict):
    content = "ts,level\n" + "never,INFO\n" * 30

    result = imported(send(client, alice, "logs", "bad.csv", content))

    assert (result["imported"], result["failed"], len(result["errors"])) == (0, 30, 20)


BOOM = '{"time": "2026-10-06T14:00:00Z", "level": "ERROR", "message": "boom"}'


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("semicolons.csv", "time;level;message\n2026-10-06 14:00:00;ERROR;boom\n"),
        ("tabs.tsv", "time\tlevel\tmessage\n2026-10-06T14:00:00\tERROR\tboom\n"),
        ("list.json", f"[{BOOM}]"),
        ("wrapped.json", f'{{"logs": [{BOOM}]}}'),
        ("lines.json", f"{BOOM}\n{BOOM}"),  # JSONL, named .json
        ("app.yaml", "- time: 2026-10-06T14:00:00Z\n  level: ERROR\n  message: boom\n"),
        (
            "nested.ndjson",
            '{"@timestamp": 1791295200000, "log": {"level": "ERROR"}, "msg": "boom"}',
        ),
        ("app.log", BOOM.replace("Z", "+00:00")),
    ],
)
def test_reads_csv_jsonl_json_and_yaml(
    client: TestClient, alice: dict, filename: str, content: str
):
    result = imported(send(client, alice, "logs", filename, content))

    assert result["imported"] >= 1 and result["failed"] == 0
    line = lines(client, alice)[0]
    assert (line["ts"], line["level"], line["message"]) == ("2026-10-06T14:00:00Z", "ERROR", "boom")


def test_uses_the_mapping_sent(client: TestClient, alice: dict):
    content = "when,lvl,text,who\n2026-10-06T14:00:00Z,ERROR,boom,api-1\n"
    mapping = {"ts": "when", "level": "lvl", "message": "text", "service": ""}  # "" is unmapped

    preview = send(client, alice, "logs", "x.csv", content, mapping, preview=True).json()
    result = imported(send(client, alice, "logs", "x.csv", content, mapping))

    assert preview["mapping"] == {"ts": "when", "level": "lvl", "message": "text"}
    assert result["imported"] == 1
    assert lines(client, alice)[0]["attrs"] == {"who": "api-1"}


@pytest.mark.parametrize(
    ("mapping", "problem"),
    [
        ({"ts": "timestamp"}, "Choose the column that holds level."),
        ({"message": "msg"}, "Choose the column that holds ts and level."),
        ({"ts": "timestamp", "level": "nope"}, "The file has no column 'nope'."),
        ({"ts": "timestamp", "level": "severity", "colour": "region"}, "There's no field 'colour'"),
    ],
)
def test_a_mapping_without_what_is_needed_is_refused(
    client: TestClient, alice: dict, mapping: dict, problem: str
):
    preview = send(client, alice, "logs", "access.csv", ACCESS_LOG, mapping, preview=True)
    upload = send(client, alice, "logs", "access.csv", ACCESS_LOG, mapping)

    # The preview still shows the file, so the mapping can be fixed.
    assert preview.json()["mapping_error"].startswith(problem)
    assert preview.json()["result"] is None and preview.json()["columns"]
    assert upload.status_code == 422
    assert upload.json()["detail"].startswith(problem)
    assert count(LogEntry, alice["project_id"]) == 0


@pytest.mark.parametrize(
    ("filename", "content", "problem"),
    [
        (
            "latin1.csv",
            "ts,level\n2026-10-06,caf\xe9\n".encode("latin-1"),
            "The file isn't UTF-8 text.",
        ),  # noqa: E501
        ("empty.jsonl", "\n  \n", "The file is empty."),
        ("broken.json", '[{"ts": 1,}]', "This isn't valid JSON: Expecting property name"),
        ("broken.yaml", "- ts: [1\n", "This isn't valid YAML:"),
        (
            "bomb.yaml",
            "a: &a [x, x]\nb: &b [*a, *a]\nc: [*b, *b]\n",
            "This isn't valid YAML: aliases",
        ),
        ("twice.csv", "ts,level,ts\n1,INFO,2\n", "The column 'ts' appears twice in the header."),
        ("number.json", "42", "The file should hold a list of records."),
    ],
)
def test_a_file_it_cant_read_is_refused(
    client: TestClient, alice: dict, filename: str, content: str | bytes, problem: str
):
    for preview in (True, False):
        response = send(client, alice, "logs", filename, content, preview=preview)

        assert response.status_code == 422, response.text
        assert response.json()["detail"].startswith(problem)


def test_size_and_row_limits(client: TestClient, alice: dict, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(uploads, "MAX_BYTES", 100)
    too_big = send(client, alice, "logs", "access.csv", ACCESS_LOG)
    monkeypatch.setattr(uploads, "MAX_BYTES", 10_000)
    monkeypatch.setitem(uploads.MAX_ROWS, uploads.UploadKind.LOGS, 2)
    too_long = send(client, alice, "logs", "access.csv", ACCESS_LOG)

    assert too_big.status_code == 413
    assert too_big.json()["detail"].startswith("The file is larger than 0 MB")
    assert too_long.status_code == 422
    assert too_long.json()["detail"].startswith("The file has 3 rows, and at most 2 logs")


def test_only_the_owner_can_upload(client: TestClient, alice: dict, new_user: NewUser):
    stranger = {**alice, "headers": new_user()}
    nobody = {**alice, "headers": {}}

    for preview in (True, False):
        assert (
            send(client, stranger, "logs", "a.csv", ACCESS_LOG, preview=preview).status_code == 404
        )
        assert send(client, nobody, "logs", "a.csv", ACCESS_LOG, preview=preview).status_code == 401
        assert (
            send(client, alice, "memories", "a.csv", ACCESS_LOG, preview=preview).status_code == 422
        )


# --- Incidents ----------------------------------------------------------------------------------


def test_the_demo_incident_history_uploads_and_the_agent_finds_it(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    content = REAL_INCIDENTS.read_bytes()

    preview = send(client, alice, "incidents", "incidents.json", content, preview=True).json()
    result = imported(send(client, alice, "incidents", "incidents.json", content))

    assert preview["mapping"]["occurred_at"] == "date"
    assert (result["imported"], result["failed"]) == (15, 0)
    assert embedder.calls == 1  # all 15 embedded together
    listed = client.get(f"{alice['url']}/incidents", headers=alice["headers"]).json()
    pool = next(item for item in listed if item["key"] == "INC-017")
    assert pool["status"] == "resolved"  # it's history
    assert pool["occurred_at"].endswith("T00:00:00Z") and pool["tags"]

    async def agent(session: AsyncSession) -> dict:
        context = ToolContext(session, embedder, uuid.UUID(alice["project_id"]), Path("."))
        return await search_previous_incidents(context, SearchArgs(query=pool["title"]))

    # Every fake embedding is the same, so only the text search ranks: INC-017 is among the best.
    assert "INC-017" in [found["ref"] for found in db(agent)["results"]]


def test_an_incident_already_in_the_project_is_never_overwritten(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    mine = {"key": "INC-017", "title": "Edited in the app"}
    client.post(f"{alice['url']}/incidents", headers=alice["headers"], json=mine)
    content = "key,title\nINC-017,From the file\nINC-090,Disk full\n"

    result = imported(send(client, alice, "incidents", "history.csv", content))

    assert (result["imported"], result["skipped"]) == (1, 1)
    listed = client.get(f"{alice['url']}/incidents", headers=alice["headers"]).json()
    assert {item["key"]: item["title"] for item in listed} == {
        "INC-017": "Edited in the app",
        "INC-090": "Disk full",
    }


def test_incident_rows_are_checked_as_in_the_app(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    content = """id,summary,labels,state,commit,date
inc-20,Pool exhausted,"db, pool; payments",Investigating,,2025-11-04
incident 21,Bad key,,,,
INC-22,,,,,
INC-23,Bad status,,closed,,
INC-24,Bad commit,,,not-a-sha,
INC-25,Bad date,,,,someday
INC-20,Twice,,,,
"""

    result = imported(send(client, alice, "incidents", "history.csv", content))

    assert (result["imported"], result["failed"]) == (1, 6)
    assert [(e["row"], e["message"].split(":")[0]) for e in result["errors"]] == [
        (2, "key"),
        (3, "title"),
        (4, "status"),
        (5, "commit_sha"),
        (6, "occurred_at"),
        (7, "INC-20 is also on row 1"),
    ]
    assert result["errors"][0]["message"] == "key: should look like INC-017"
    [incident] = client.get(f"{alice['url']}/incidents", headers=alice["headers"]).json()
    assert incident | {"id": None, "created_at": None} == {
        "id": None,
        "key": "INC-20",
        "title": "Pool exhausted",
        "severity": None,
        "symptoms": None,
        "root_cause": None,
        "fix": None,
        "commit_sha": None,
        "tags": ["db", "pool", "payments"],
        "status": "investigating",
        "occurred_at": "2025-11-04T00:00:00Z",
        "created_at": None,
    }


def test_reads_yaml_incident_history(client: TestClient, alice: dict, embedder: FakeEmbedder):
    content = """incidents:
  - key: INC-031
    title: Webhook 500s
    date: 2026-02-03
    tags: [webhooks, "500"]
    root_cause: None access on an optional field
"""

    result = imported(send(client, alice, "incidents", "history.yml", content))

    assert result["imported"] == 1
    [incident] = client.get(f"{alice['url']}/incidents", headers=alice["headers"]).json()
    assert incident["occurred_at"] == "2026-02-03T00:00:00Z"
    assert incident["tags"] == ["webhooks", "500"]
    assert incident["root_cause"] == "None access on an optional field"


def test_if_indexing_fails_no_incident_is_saved(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    request = httpx2.Request("POST", "https://api.tokenfactory.nebius.com/v1/embeddings")
    embedder.fail = openai.APIConnectionError(request=request)

    response = send(client, alice, "incidents", "history.csv", "key,title\nINC-1,Down\n")

    assert response.status_code == 502
    assert response.json()["detail"].startswith("Couldn't index the incidents for search")
    assert count(Incident, alice["project_id"]) == 0


def test_a_preview_of_incidents_embeds_nothing(
    client: TestClient, alice: dict, embedder: FakeEmbedder
):
    preview = send(client, alice, "incidents", "h.csv", "key,title\nINC-1,Down\n", preview=True)

    assert preview.json()["result"]["imported"] == 1
    assert embedder.calls == 0
    assert count(Incident, alice["project_id"]) == 0
