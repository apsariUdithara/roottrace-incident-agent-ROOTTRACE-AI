# backend

FastAPI app (Python 3.12, `uv`). It serves the REST API and SSE stream, and runs the LangGraph agent, RAG, tools, sandbox runners and MCP servers. See [IMPLEMENTATION_PLAN.md §2](../docs/IMPLEMENTATION_PLAN.md#2-repository-layout-monorepo).

| Folder (under `app/`) | Owner |
|---|---|
| `core/`, `models/`, `schemas/`, `api/routes/`, plus `alembic/` | Person B |
| `llm/`, `prompts/`, `rag/`, `tools/`, `agent/`, `memory/`, `sandbox/`, `mcp_servers/`, `observability/` | Person A |
| `main.py`, `agent/events.py` | Shared |

No PayStream names, paths or hints anywhere under `app/`. CI enforces this.
