# syntax=docker/dockerfile:1
# The API server and agent. Build from the repo root:
#   docker build -f backend/Dockerfile -t roottrace-backend .
#
# The image also carries the PayStream demo repository, built here from its scripts (git only,
# free): the agent reads the code and history of the repo it investigates from disk. Fixed dates
# and authors keep its commit SHAs the same on every rebuild.

FROM python:3.12-slim AS base
# git: the agent's tools read history with it, and the Fix Generator and PR step apply patches.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend

# Dependencies on their own layer, so a code change doesn't reinstall them.
FROM base AS deps
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Build PayStream's history and check every release (visible tests pass, hidden test fails).
# pytest is needed only for that check, so it stays in this stage.
FROM deps AS paystream
COPY demo-target /app/demo-target
COPY eval/hidden_tests /app/eval/hidden_tests
RUN --mount=type=cache,target=/root/.cache/uv \
    uv run --no-sync --with pytest python /app/demo-target/scripts/build_history.py

FROM base AS runtime
RUN useradd --create-home --uid 1000 roottrace
COPY --from=deps /app/backend/.venv /app/backend/.venv
COPY backend /app/backend
COPY demo-target /app/demo-target
# The agent's PR step adds worktrees inside this repo, so the app user must be able to write it.
COPY --from=paystream --chown=roottrace /app/demo-target/build /app/demo-target/build
# Clones of connected repositories (DATA_DIR). Mount a volume here to keep them across deploys.
RUN mkdir /app/backend/data && chown roottrace /app/backend/data
ENV PATH="/app/backend/.venv/bin:$PATH"
USER roottrace
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
