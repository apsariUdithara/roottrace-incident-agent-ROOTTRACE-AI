"""The tool registry: typed tools the agent can call, and one safe way to call them.

A tool is an async function `fn(ctx, args) -> dict`, where `args` is a Pydantic model. The
model's field descriptions and the tool's description are what the LLM reads to decide when and
how to call it, so they are written for the model, not for us.

`call_tool` is the only entry point. It never raises: bad arguments, guard violations and
crashes all come back as {"error": ...}, so the agent can read the problem and try again.
"""

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMRouter, started_at

MAX_RESULT_CHARS = 8000  # cap on what goes back to the LLM per call (about 2,000 tokens)


class ToolError(Exception):
    """A problem the agent should see and can fix, such as a path outside the repository."""


@dataclass
class ToolContext:
    """What every tool may use. Built once per investigation."""

    session: AsyncSession
    router: LLMRouter
    project_id: uuid.UUID
    repo_path: Path  # local clone of the project's repository
    ref: str = "HEAD"  # the deployed version under investigation, e.g. a release tag
    trace: list[dict] = field(default_factory=list)  # one entry per tool call


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[[ToolContext, BaseModel], Awaitable[dict]]

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args.model_json_schema(),
            },
        }


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, args: type[BaseModel]):
    """Register an async function as a tool."""

    def register(fn):
        TOOLS[name] = Tool(name, description, args, fn)
        return fn

    return register


def openai_schemas(names: list[str] | None = None) -> list[dict]:
    """Tool definitions for the LLM. Pass `names` to offer only the tools a step needs."""
    return [TOOLS[n].openai_schema() for n in (names or TOOLS)]


def _json_default(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _fit(result: dict) -> str:
    text = json.dumps(result, default=_json_default)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + ' ... [truncated: narrow the query or ask for fewer results]"'


async def call_tool(ctx: ToolContext, name: str, arguments: str | dict) -> str:
    """Run one tool call from the LLM and return its result as JSON text for the LLM."""
    start = time.perf_counter()
    name = name.removeprefix("functions.")  # models sometimes copy the API's namespace prefix
    if name not in TOOLS:
        result = {"error": f"unknown tool {name!r}. Available: {', '.join(TOOLS)}"}
    else:
        try:
            raw = json.loads(arguments) if isinstance(arguments, str) else arguments
            args = TOOLS[name].args.model_validate(raw or {})
            result = await TOOLS[name].fn(ctx, args)
        except (ValidationError, json.JSONDecodeError) as exc:
            result = {"error": f"invalid arguments: {exc}"}
        except ToolError as exc:
            result = {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - report, don't crash the investigation
            result = {"error": f"{type(exc).__name__}: {exc}"}
    text = _fit(result)
    ctx.trace.append(
        {
            "tool": name,
            "arguments": arguments,
            "ok": "error" not in result,
            "latency_ms": (latency := int((time.perf_counter() - start) * 1000)),
            "started_at": started_at(latency),
            "result_chars": len(text),
        }
    )
    return text
