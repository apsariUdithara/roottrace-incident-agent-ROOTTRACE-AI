# backend

FastAPI app (Python 3.12, `uv`). It serves the REST API and SSE stream, and runs the LangGraph agent, RAG, tools, sandbox runners and MCP servers. See [IMPLEMENTATION_PLAN.md §2](../docs/IMPLEMENTATION_PLAN.md#2-repository-layout-monorepo).

| Folder (under `app/`) | Owner |
|---|---|
| `core/`, `models/`, `schemas/`, `api/routes/`, plus `alembic/` | Person B |
| `llm/`, `prompts/`, `rag/`, `tools/`, `agent/`, `memory/`, `sandbox/`, `mcp_servers/`, `observability/` | Person A |
| `main.py`, `agent/events.py` | Shared |

No PayStream names, paths or hints anywhere under `app/`. CI enforces this.

## Run locally

Start the database first (from the repo root): `docker compose -f infra/docker-compose.yml up -d --wait`. Then, from `backend/`:

```bash
uv sync                                   # installs Python 3.12 and dependencies
cp .env.example .env                      # then fill in the secrets
uv run uvicorn app.main:app --reload      # http://localhost:8000/health
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
