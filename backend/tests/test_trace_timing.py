"""Spans keep when each call started, so the Trace tab's waterfall has real offsets.

They are saved together at the end of a run; before this, the database's now() gave every span
of a run the same start time."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.llm.router import started_at
from app.tools.registry import ToolContext, call_tool


def test_a_call_started_its_latency_before_now():
    assert abs(datetime.now(UTC) - timedelta(seconds=1.5) - started_at(1500)) < timedelta(seconds=1)


def test_each_tool_call_records_its_own_start_time():
    ctx = ToolContext(session=None, router=None, project_id=uuid.uuid4(), repo_path=Path("."))
    before = datetime.now(UTC)
    asyncio.run(call_tool(ctx, "no_such_tool", {}))  # still traced, as a failed call
    asyncio.run(call_tool(ctx, "no_such_tool", {}))

    first, second = (t["started_at"] for t in ctx.trace)
    assert before - timedelta(seconds=1) <= first <= second <= datetime.now(UTC)
