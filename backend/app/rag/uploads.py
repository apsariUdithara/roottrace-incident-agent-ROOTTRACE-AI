"""Bring your own data: logs and incident history from a file, into the tables the agent reads.

Log lines go into log_entries, where search_logs finds them; incidents into incidents, indexed
for search_previous_incidents like the ones added in the app. A file goes through three steps:

1. Read its rows: CSV (with a header row), JSONL, JSON or YAML. Nested keys become columns such
   as "http.status".
2. Map our fields to its columns: the mapping sent, or one guessed from the column names.
3. Check each row. A row with a problem is reported and left out; the others are imported. Rows
   already in the project are skipped, so uploading a file twice imports it once.

plan() does all of it without saving; save() then writes what it found.
"""

import csv
import io
import json
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMRouter
from app.models import Incident, IncidentStatus, LogEntry
from app.rag.ingest import index_incidents
from app.schemas.incident import IncidentCreate
from app.schemas.knowledge import UploadFormat, UploadKind, UploadResult, UploadRowError

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = {UploadKind.LOGS: 100_000, UploadKind.INCIDENTS: 500}
MAX_COLUMNS = 200
MAX_ERRORS = 20
SAMPLE_ROWS = 5
NESTING = 3  # levels of nested keys made into columns; anything deeper is kept as it is
MAX_MESSAGE = 20_000
MAX_INT = 2**31 - 1  # the columns are 32-bit


class UploadError(Exception):
    """The file as a whole can't be imported. The message says why, for a person."""


class RowProblem(Exception):
    """One row can't be imported. The message says why, e.g. "ts: not a time"."""


@dataclass(frozen=True)
class Target:
    """One of our fields, and the column names it's guessed from."""

    name: str
    description: str
    required: bool = False
    aliases: tuple[str, ...] = ()


LOG_FIELDS = (
    Target(
        "ts",
        "When it was logged: ISO 8601 (UTC if no timezone), or seconds or milliseconds since 1970",
        True,
        ("timestamp", "@timestamp", "time", "datetime", "date", "logged_at"),
    ),
    Target("level", "As logged: INFO, WARN, ERROR…", True, ("severity", "lvl", "log.level")),
    Target("service", "The service that logged it", aliases=("service.name", "app", "application")),
    Target("instance", "Host, pod or instance", aliases=("host", "hostname", "host.name", "pod")),
    Target("route", "HTTP route, e.g. /payments", aliases=("path", "endpoint", "url", "url.path")),
    Target(
        "status",
        "HTTP status code",
        aliases=("status_code", "http_status", "http.status_code", "http.response.status_code"),
    ),
    Target(
        "latency_ms",
        "How long it took, in milliseconds",
        aliases=("latency", "duration_ms", "duration", "elapsed_ms", "response_time_ms"),
    ),
    Target("message", "The log message", aliases=("msg", "log", "text", "event")),
    Target(
        "error_type",
        "e.g. OperationalError",
        aliases=("error", "error.type", "exception", "exception_type", "exception.type"),
    ),
    Target(
        "trace_id",
        "Ties the lines of one request together",
        aliases=("trace", "trace.id", "request_id", "correlation_id"),
    ),
)
INCIDENT_FIELDS = (
    Target(
        "key",
        "e.g. INC-017: letters, a dash and a number",
        True,
        ("id", "incident", "incident_id", "incident_key", "number", "ticket"),
    ),
    Target("title", "A one-line summary", True, ("summary", "name", "subject")),
    Target("severity", "e.g. SEV2", aliases=("sev", "priority")),
    Target("symptoms", "What was seen", aliases=("description", "impact", "details")),
    Target("root_cause", "Why it happened", aliases=("cause", "rca")),
    Target("fix", "What fixed it", aliases=("resolution", "remediation", "solution")),
    Target("commit_sha", "The commit that caused or fixed it", aliases=("commit", "sha")),
    Target("tags", "A list, or labels separated by commas", aliases=("labels", "tag")),
    Target("status", "open, investigating or resolved (resolved if empty)", aliases=("state",)),
    Target(
        "occurred_at",
        "When it happened (UTC if no timezone)",
        aliases=("date", "started_at", "start", "time", "timestamp", "opened_at", "created_at"),
    ),
)
FIELDS = {UploadKind.LOGS: LOG_FIELDS, UploadKind.INCIDENTS: INCIDENT_FIELDS}

Row = tuple[int, dict[str, Any] | str]  # its number, and its values or why it can't be read


# --- 1. Reading --------------------------------------------------------------------------------

_SUFFIXES = {
    ".csv": UploadFormat.CSV,
    ".jsonl": UploadFormat.JSONL,
    ".ndjson": UploadFormat.JSONL,
    ".json": UploadFormat.JSON,
    ".yaml": UploadFormat.YAML,
    ".yml": UploadFormat.YAML,
}


class _SafeLoader(yaml.SafeLoader):
    """safe_load without anchors and aliases: aliases can make a small file expand without end."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            mark = self.peek_event().start_mark
            raise yaml.composer.ComposerError(None, None, "aliases (*name) aren't supported", mark)
        return super().compose_node(parent, index)


def file_format(filename: str, text: str) -> UploadFormat:
    """By the file's extension; else by its first character (a .log or .txt file)."""
    suffix = filename[filename.rfind(".") :].lower() if "." in filename else ""
    if suffix in _SUFFIXES:
        return _SUFFIXES[suffix]
    first = text.lstrip()[:1]
    return {"{": UploadFormat.JSONL, "[": UploadFormat.JSON}.get(first, UploadFormat.CSV)


def read_rows(filename: str, data: bytes) -> tuple[UploadFormat, list[Row]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UploadError("The file isn't UTF-8 text.") from None
    if not text.strip():
        raise UploadError("The file is empty.")
    format_ = file_format(filename, text)
    try:
        match format_:
            case UploadFormat.CSV:
                return format_, _csv_rows(text)
            case UploadFormat.JSONL:
                return format_, _jsonl_rows(text)
            case UploadFormat.JSON:
                try:
                    return format_, _records(json.loads(text))
                except json.JSONDecodeError as exc:
                    if exc.msg != "Extra data" or not text.lstrip().startswith("{"):
                        raise
                    return UploadFormat.JSONL, _jsonl_rows(text)  # JSONL named .json
            case UploadFormat.YAML:
                return format_, _records(yaml.load(text, Loader=_SafeLoader))
    except json.JSONDecodeError as exc:
        raise UploadError(f"This isn't valid JSON: {exc.msg} (line {exc.lineno}).") from None
    except yaml.MarkedYAMLError as exc:
        line = f" (line {exc.problem_mark.line + 1})" if exc.problem_mark else ""
        raise UploadError(f"This isn't valid YAML: {exc.problem}{line}.") from None
    except yaml.YAMLError as exc:
        raise UploadError(f"This isn't valid YAML: {exc}.") from None
    except csv.Error as exc:
        raise UploadError(f"This isn't valid CSV: {exc}.") from None
    except RecursionError:
        raise UploadError("The file is nested too deeply to read.") from None
    raise AssertionError(format_)


def _csv_rows(text: str) -> list[Row]:
    try:  # commas, or semicolons, tabs or pipes
        dialect = csv.Sniffer().sniff(text[:10_000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text, newline=""), dialect)
    header = next(reader, [])
    names = [name.strip() for name in header]
    repeated = [name for name, n in Counter(filter(None, names)).items() if n > 1]
    if repeated:
        raise UploadError(f"The column {repeated[0]!r} appears twice in the header.")
    if not any(names):
        raise UploadError("The first row should name the columns.")
    rows: list[Row] = []
    for number, cells in enumerate(reader, 1):
        if not any(cell.strip() for cell in cells):
            continue  # a blank line
        if len(cells) > len(names):
            rows.append((number, f"{len(cells)} values, but the header names {len(names)}"))
            continue
        values = {name: cell for name, cell in zip(names, cells, strict=False) if name}
        rows.append((number, values))
    return rows


def _jsonl_rows(text: str) -> list[Row]:
    rows: list[Row] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            rows.append((number, "not valid JSON"))
            continue
        rows.append((number, _flat(value) if isinstance(value, dict) else "not a JSON object"))
    return rows


def _records(document: Any) -> list[Row]:
    """The rows of a JSON or YAML document: a list of objects, or an object holding one, such
    as {"incidents": [...]}, or a single object."""
    if isinstance(document, dict):
        lists = [
            value
            for value in document.values()
            if isinstance(value, list) and value and all(isinstance(v, dict) for v in value)
        ]
        document = lists[0] if len(lists) == 1 else [document]
    if not isinstance(document, list):
        raise UploadError("The file should hold a list of records.")
    return [
        (number, _flat(item) if isinstance(item, dict) else "not an object with fields")
        for number, item in enumerate(document, 1)
    ]


def _flat(record: dict, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """{"http": {"status": 500}} -> {"http.status": 500}. Values are made JSON-friendly."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value and depth < NESTING:
            flat.update(_flat(value, f"{name}.", depth + 1))
        else:
            flat[name] = _plain(value)
    return flat


def _plain(value: Any) -> Any:
    """YAML's dates as ISO strings, and NaN as text, so every value can be stored as JSON."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


# --- 2. Mapping --------------------------------------------------------------------------------


def _normal(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def suggest_mapping(kind: UploadKind, columns: list[str]) -> dict[str, str]:
    """Our fields to the columns whose names match theirs or a usual alias, e.g. ts <- timestamp.
    A column fills one field at most."""
    by_name: dict[str, str] = {}
    for column in columns:
        by_name.setdefault(_normal(column), column)
    mapping: dict[str, str] = {}
    for target in FIELDS[kind]:
        for candidate in (target.name, *target.aliases):
            column = by_name.get(_normal(candidate))
            if column and column not in mapping.values():
                mapping[target.name] = column
                break
    return mapping


def mapping_problem(kind: UploadKind, mapping: dict[str, str], columns: list[str]) -> str | None:
    names = [target.name for target in FIELDS[kind]]
    for name, column in mapping.items():
        if name not in names:
            return f"There's no field {name!r} for {kind.value}. The fields: {', '.join(names)}."
        if column not in columns:
            return f"The file has no column {column!r}."
    missing = [t.name for t in FIELDS[kind] if t.required and t.name not in mapping]
    if missing:
        return f"Choose the column that holds {' and '.join(missing)}."
    return None


# --- 3. Checking rows --------------------------------------------------------------------------


def _given(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _short(name: str, value: Any, limit: int) -> str | None:
    if not _given(value):
        return None
    text = _text(value)
    if len(text) > limit:
        raise RowProblem(f"{name}: longer than {limit:,} characters")
    return text


def _whole(name: str, value: Any) -> int | None:
    if not _given(value):
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
    except (TypeError, ValueError):
        raise RowProblem(f"{name}: not a number") from None
    if not math.isfinite(number) or not 0 <= number <= MAX_INT:
        raise RowProblem(f"{name}: out of range")
    return round(number)


def parse_time(name: str, value: Any) -> datetime:
    """ISO 8601 (UTC without a timezone), or seconds or milliseconds since 1970."""
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, int | float) or re.fullmatch(r"\d{9,13}(\.\d+)?", _text(value)):
            number = float(value)
            return datetime.fromtimestamp(number / 1000 if number > 1e11 else number, UTC)
        time = datetime.fromisoformat(_text(value))
    except (ValueError, OverflowError, OSError):
        raise RowProblem(f"{name}: not a time") from None
    return time if time.tzinfo else time.replace(tzinfo=UTC)


def log_line(values: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """The log_entries row for one record. Columns not mapped are kept in `attrs`."""
    get = {name: values.get(column) for name, column in mapping.items()}
    if not _given(get.get("ts")):
        raise RowProblem("ts: missing")
    level = _short("level", get.get("level"), 16)
    if level is None:
        raise RowProblem("level: missing")
    mapped = set(mapping.values())
    return {
        "ts": parse_time("ts", get["ts"]),
        "level": level,
        "service": _short("service", get.get("service"), 200),
        "instance": _short("instance", get.get("instance"), 200),
        "route": _short("route", get.get("route"), 500),
        "status": _whole("status", get.get("status")),
        "latency_ms": _whole("latency_ms", get.get("latency_ms")),
        "message": _short("message", get.get("message"), MAX_MESSAGE) or "",
        "error_type": _short("error_type", get.get("error_type"), 200),
        "trace_id": _short("trace_id", get.get("trace_id"), 100),
        "attrs": {k: v for k, v in values.items() if k not in mapped and _given(v)},
    }


# What makes two log lines the same line (attrs aside).
_IDENTITY = (
    "ts",
    "level",
    "service",
    "instance",
    "route",
    "status",
    "latency_ms",
    "message",
    "error_type",
    "trace_id",
)


def incident(values: dict[str, Any], mapping: dict[str, str]) -> IncidentCreate:
    """The incident for one record, checked as when it's added in the app."""
    data: dict[str, Any] = {}
    for name, column in mapping.items():
        value = values.get(column)
        if not _given(value):
            continue
        if name == "tags":
            items = value if isinstance(value, list) else re.split(r"[,;|]", _text(value))
            data[name] = [_text(item) for item in items if _given(item)]
        elif name == "occurred_at":
            data[name] = parse_time(name, value)
        elif name == "status":
            data[name] = _text(value).lower()
        else:
            data[name] = _text(value)
    data.setdefault("status", IncidentStatus.RESOLVED)  # incident history: they're over
    try:
        return IncidentCreate.model_validate(data)
    except ValidationError as exc:
        error = exc.errors()[0]
        where = ".".join(str(part) for part in error["loc"])
        if where == "key" and error["type"] == "string_pattern_mismatch":
            raise RowProblem("key: should look like INC-017") from None
        raise RowProblem(f"{where}: {error['msg']}") from None


# --- Plan and save -----------------------------------------------------------------------------


@dataclass
class Plan:
    format: UploadFormat
    rows: int
    columns: list[str]
    sample: list[dict[str, Any]]
    mapping: dict[str, str]
    mapping_error: str | None = None
    result: UploadResult | None = None
    new: list = field(default_factory=list)  # log_entries rows, or IncidentCreate


async def plan(
    session: AsyncSession,
    project_id,
    kind: UploadKind,
    filename: str,
    data: bytes,
    mapping: dict[str, str] | None,
) -> Plan:
    """Read, map and check the file, without saving anything. Raises UploadError."""
    format_, rows = read_rows(filename, data)
    if len(rows) > MAX_ROWS[kind]:
        raise UploadError(
            f"The file has {len(rows):,} rows, and at most {MAX_ROWS[kind]:,} {kind.value} can "
            "be uploaded at once. Split it, and upload the parts one by one."
        )
    records = [values for _, values in rows if isinstance(values, dict)]
    columns = list(dict.fromkeys(column for values in records for column in values))
    if len(columns) > MAX_COLUMNS:
        raise UploadError(f"The file has {len(columns)} columns; at most {MAX_COLUMNS} are read.")
    if mapping is None:
        mapping = suggest_mapping(kind, columns)
    mapping = {name: column for name, column in mapping.items() if column}  # "" is unmapped
    result = Plan(format_, len(rows), columns, records[:SAMPLE_ROWS], mapping)
    result.mapping_error = mapping_problem(kind, mapping, columns)
    if result.mapping_error:
        return result

    convert = log_line if kind is UploadKind.LOGS else incident
    checked: list[tuple[int, Any]] = []
    problems: list[UploadRowError] = []
    failed = 0
    keys: dict[str, int] = {}  # incident key -> the row it's on
    for number, values in rows:
        try:
            if isinstance(values, str):
                raise RowProblem(values)
            item = convert(values, mapping)
            if isinstance(item, IncidentCreate):
                if item.key in keys:
                    raise RowProblem(f"{item.key} is also on row {keys[item.key]}")
                keys[item.key] = number
            checked.append((number, item))
        except RowProblem as exc:
            failed += 1
            if len(problems) < MAX_ERRORS:
                problems.append(UploadRowError(row=number, message=str(exc)))

    items = [item for _, item in checked]
    if kind is UploadKind.LOGS:
        result.new = await _new_lines(session, project_id, items)
    else:
        result.new = await _new_incidents(session, project_id, items)
    result.result = UploadResult(
        kind=kind,
        filename=filename,
        imported=len(result.new),
        skipped=len(items) - len(result.new),
        failed=failed,
        errors=problems,
    )
    return result


async def _new_lines(session: AsyncSession, project_id, lines: list[dict]) -> list[dict]:
    """The lines not already in the project's logs. A line logged twice in the file is kept
    twice, unless the logs already have it twice."""
    if not lines:
        return []
    first, last = min(line["ts"] for line in lines), max(line["ts"] for line in lines)
    stored = await session.execute(
        select(*(getattr(LogEntry, name) for name in _IDENTITY)).where(
            LogEntry.project_id == project_id, LogEntry.ts >= first, LogEntry.ts <= last
        )
    )
    existing = Counter(tuple(row) for row in stored)
    new = []
    for line in lines:
        identity = tuple(line[name] for name in _IDENTITY)
        if existing[identity]:
            existing[identity] -= 1
        else:
            new.append(line)
    return new


async def _new_incidents(
    session: AsyncSession, project_id, incidents: list[IncidentCreate]
) -> list[IncidentCreate]:
    """The incidents whose keys the project doesn't have yet: one edited in the app is never
    overwritten by an upload."""
    keys = [item.key for item in incidents]
    taken = set(
        await session.scalars(
            select(Incident.key).where(Incident.project_id == project_id, Incident.key.in_(keys))
        )
    )
    return [item for item in incidents if item.key not in taken]


async def save(
    session: AsyncSession,
    router: Callable[[], LLMRouter],
    project_id,
    kind: UploadKind,
    found: Plan,
) -> None:
    """Write what plan() found. Incidents are embedded too, with `router()`: if that fails, it
    raises and the caller saves nothing."""
    if not found.new:
        return
    if kind is UploadKind.LOGS:
        await session.execute(
            insert(LogEntry), [{"project_id": project_id, **line} for line in found.new]
        )
        return
    incidents = [Incident(project_id=project_id, **item.model_dump()) for item in found.new]
    session.add_all(incidents)
    await session.flush()
    await index_incidents(session, router(), incidents)
