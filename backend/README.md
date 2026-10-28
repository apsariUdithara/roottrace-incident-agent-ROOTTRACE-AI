# backend

FastAPI app (Python 3.12, `uv`). It serves the REST API and SSE stream, and runs the LangGraph agent, RAG, tools, sandbox runners and MCP servers. See [IMPLEMENTATION_PLAN.md §2](../docs/IMPLEMENTATION_PLAN.md#2-repository-layout-monorepo).

| Folder (under `app/`) | Owner |
|---|---|
| `core/`, `models/`, `schemas/`, `api/routes/`, plus `alembic/` | Person B |
| `llm/`, `rag/`, `tools/`, `agent/` (graph, fix, memory, follow-ups, PR), `sandbox/`, `mcp_servers.py` | Person A |
| `main.py`, `agent/events.py`, `api/routes/investigations.py` (A wrote the built routes) | Shared |

No PayStream names, paths or hints anywhere under `app/`. CI enforces this.

## Run locally

Start the database first (from the repo root): `docker compose -f infra/docker-compose.yml up -d --wait`. Then, from `backend/`:

```bash
uv sync                                   # installs Python 3.12 and dependencies
cp .env.example .env                      # then fill in the secrets
uv run alembic upgrade head               # create or update the tables
uv run uvicorn app.main:app --reload      # http://localhost:8000/health
uv run pytest                             # needs Postgres running; uses a separate <db>_test database
uv run ruff check . && uv run ruff format --check .
```

`GET /health` says the process is up. `GET /health/ready` also checks the database and returns 503 if it can't be reached.

The Docker image (`backend/Dockerfile`, built from the repo root) is described in [infra/README.md](../infra/README.md#docker-images).

### Demo data and a first investigation

The agent needs an indexed repository. The app connects and indexes one (see the Repositories and Knowledge APIs below); for the demo, the scripts load PayStream with its past incidents, issues and logs too:

```bash
python ../demo-target/scripts/build_history.py   # builds demo-target/build/paystream (git only, free)
uv run python scripts/seed_demo.py               # demo user + PayStream indexed at v1.4.8 (~$0.0003)
uv run python scripts/investigate.py             # one investigation in the terminal (~$0.05-0.15)
uv run python scripts/ask.py "Which commit introduced this?"
uv run python scripts/open_pr.py                 # approve the verified fix: opens a draft PR
uv run python scripts/seed_fake_investigation.py # a free replay of scenario 1 (no models, no sandbox)
```

- `seed_demo.py` creates `demo@roottrace.dev` with `DEMO_PASSWORD`, or prints a generated password once. Running it again is safe.
- The seed records the indexed release (`v1.4.8`) as PayStream's default branch. Investigations start from it, and re-indexing from the app keeps it: PayStream's `main` is the healthy service, without the bug.
- Sandbox runs use the ConTree CLI's saved login (`contree auth`), or `NEBIUS_API_KEY` plus `NEBIUS_AI_PROJECT`.
- `open_pr.py` needs `GITHUB_TOKEN`: a fine-grained token for the demo repo only.
- In development, model answers are cached, so re-running the same investigation costs nothing.
- **Without a Nebius key**, `seed_fake_investigation.py` adds a finished scenario-1 investigation to the demo project: 89 hand-written events (`scripts/scenario1_replay.py`) that `/events` and `/stream` serve like a real run's. It waits for approval but has no patch, so it can never open a PR. Its test runs don't claim a sandbox. Running it again replaces it. `--json` writes the events to `frontend/src/test/fixtures/scenario-1-events.json` for the frontend's tests; a backend test fails if that file is out of date.
- With `READONLY_DATABASE_URL` set, the seed also creates that role (or updates its password) and lets it SELECT from `demo_app`, and nothing else. The agent's `query_database` tool then runs as it, a fifth safety layer after the SQL checks, the read-only transaction, the timeout and the row limit (`app/tools/database.py`). The grants are made again on every seed, because rebuilding `demo_app` drops them.

## Database sessions

Routes get a session with the `SessionDep` dependency from `app/core/db.py`. There's one session per request, and it doesn't auto-commit: call `await session.commit()` yourself, and anything uncommitted is rolled back when the request ends.

## Auth

| Route | Does |
|---|---|
| `POST /auth/register` | Creates the account (email stored lowercased, password ≥ 8 chars), then logs in. 409 if the email exists. |
| `POST /auth/login` | Returns `{access_token, token_type, expires_in, user}` and sets the refresh cookie. |
| `POST /auth/refresh` | Uses the refresh cookie to return a new access token, and replaces the cookie (rotation). |
| `POST /auth/logout` | Clears the refresh cookie (204). |
| `GET /auth/me` | The current user. |

- The access token (15 min) goes in `Authorization: Bearer ...`. The frontend keeps it in memory, and on a 401 or a page reload calls `/auth/refresh`.
- The refresh token (7 days) is the `roottrace_refresh` cookie: httpOnly, `SameSite=Strict`, and `Secure` in production. JavaScript never sees it.
- To require a logged-in user in a route, add a `user: CurrentUser` parameter (from `app/api/deps.py`). It returns 401 with `WWW-Authenticate: Bearer` otherwise.
- **Scoping (every data route):** under `/projects/{project_id}/...`, take a `project: OwnedProject` parameter. It returns the project only if the caller owns it, and gives the same 404 for "someone else's" and "doesn't exist". For other queries, start from `owned_select(Model, user)`, which works for `Project` and any table with a `project_id`. For example, `owned_select(Investigation, user).where(Investigation.id == investigation_id)`. Never query a project-scoped table without it.
- Wrong password and unknown email give the same 401 in the same time (argon2 runs either way).
- Not yet done: rate limiting on login (planned with the demo endpoint), and server-side token revocation. Logout clears the cookie, and an access token already issued stays valid until it expires.

## Repositories API

`app/api/routes/repositories.py`. A project has one repository for now, because the agent investigates a single codebase.

| Route | Does |
|---|---|
| `GET /projects/{id}/repositories` | The project's repository, as a list (empty, or one item). |
| `POST /projects/{id}/repositories` | `{url, default_branch?, token?, setup_command?, test_command?}`. Connects a GitHub repository (`https://github.com/<owner>/<repo>`). 409 if the project already has one. |
| `PATCH …/{repository_id}` | Changes only the fields you send. `setup_command: null` goes back to auto-detect, and `token: null` removes the token. Changing a command sets `sandbox_status` back to `unknown`, to be checked again. |
| `DELETE …/{repository_id}` | Disconnects it, and removes its docs and code from the knowledge base. Incidents, issues and logs stay. |

- **The token is write-only.** It's encrypted with `FERNET_KEY` before it's stored, and never returned: responses only say `has_token`. Spaces and line breaks around it are trimmed.
- **Connecting doesn't clone or index.** `POST /projects/{id}/ingest` does (see below). An investigation can start once the repository has been indexed.
- The stored token is used to clone and fetch. Opening a PR still uses `GITHUB_TOKEN` from the environment.

## Knowledge API

`app/api/routes/knowledge.py`.

| Route | Does |
|---|---|
| `POST /projects/{id}/ingest` | 202, with the repository as `queued`. Indexes it in the background; poll `GET …/repositories` until `ingest_status` is `indexed` or `failed` (with `ingest_error`, a readable reason). A run already going isn't started twice. 409 without a repository. |
| `GET /projects/{id}/knowledge?type=&q=&limit=&offset=` | What the agent can search: the indexed docs, code, issues and incidents, by type and then path. `q` matches the title or path, as plain text. Each item has its `chunk_count` and a `source_ref` for the source viewer. |
| `GET /projects/{id}/logs?level=&service=&route=&error_type=&text=&trace_id=&since=&until=&limit=&offset=` | The project's log lines, newest first, 100 a page (at most 500). The filters match as in the agent's `search_logs` tool, so its evidence can be checked: `level` in any case, `route` and `text` (message or error type) as part of the value, the rest exactly. `since` and `until` are included; a time without a timezone is UTC. 422 if `since` is after `until`. |
| `GET /projects/{id}/logs/facets` | What the logs hold, for the explorer's filters: the levels, services, routes and error types with how many lines have each (the 100 most frequent per field), and the first and last time. |
| `POST /projects/{id}/uploads/preview` | Multipart: `kind` (`logs` or `incidents`), `file`, and optionally `mapping` (JSON, our field to the file's column). Saves nothing. Returns the file's columns and first rows, our `fields`, the `mapping` (the one sent, or one guessed from the column names), and what importing would do: `imported`, `skipped`, `failed` and the first 20 `errors`. A mapping without a required field gives `mapping_error` instead, so the mapping can be fixed. |
| `POST /projects/{id}/uploads` | The same form; 201 with what was imported. 413 over 20 MB, 422 for a file it can't read or a mapping without a required field, 502 if uploaded incidents can't be embedded (then nothing is saved). |

**Uploads** (`app/rag/uploads.py`) put your own data where the agent looks: log lines into `log_entries` (for `search_logs`), incidents into `incidents` and the search index (for `search_previous_incidents`).
- **Formats:** CSV with a header row (commas, semicolons, tabs or pipes), JSONL, JSON or YAML (a list, or an object holding one such as `{"incidents": [...]}`). Nested keys become columns like `http.status`. YAML anchors and aliases are refused: they can make a small file expand without end.
- **Logs** need `ts` and `level`. Times are ISO 8601 (UTC without a timezone) or seconds or milliseconds since 1970. Columns that aren't mapped are kept on each line, in `attrs`. A line the project already has is skipped, so a file can be uploaded again: at most 100,000 rows a file.
- **Incidents** need `key` and `title`, and are checked as when they're added in the app. They're `resolved` unless the file says otherwise, because they're history. A key the project already has is skipped, never overwritten. They're embedded together, in a few calls: at most 500 a file.
- A row with a problem is left out and listed; the rest are imported. Uploading logs needs no Nebius key.

A run (`app/rag/indexing.py`):
1. **Clone** the repository, bare, into `DATA_DIR/repos/<id>.git` (default `backend/data`), or fetch what's new. The token goes in a header for each command, through the environment: it's never written to the clone's config. A repository without a remote, like the demo's local PayStream build, is used as it is.
2. **Export** its files at the default branch with `git archive`. The clone itself never changes, so a running investigation can keep reading it.
3. **Index** them with `ingest_repo` (unchanged files cost no embedding calls), then record the commit (`last_indexed_sha`, `last_indexed_at`) and the clone (`local_path`). All of it is saved together: a failed run leaves the previous index as it was.

- At most 2000 files to index (docs, code and config) per repository, to keep a public demo's embedding spend small.
- Runs live in the server process, like investigations. When the server starts, runs that a restart cut off are marked failed.
- Disconnecting a repository deletes the clone RootTrace made (never a repository it didn't clone).

## Incidents API

`app/api/routes/incidents.py`: a project's incident history, which the agent searches for similar past incidents.

| Route | Does |
|---|---|
| `GET /projects/{id}/incidents` | Newest first: by `occurred_at`, or else by when it was added. |
| `POST /projects/{id}/incidents` | `{key, title, severity?, symptoms?, root_cause?, fix?, commit_sha?, tags?, status?, occurred_at?}`. 409 if the project already has the key. |
| `GET …/{incident_id}` | One incident. |
| `PATCH …/{incident_id}` | Changes only the fields you send. Null clears an optional field, and leaves `key`, `title`, `tags` and `status` unchanged. |
| `DELETE …/{incident_id}` | Deletes it. Investigations that linked it keep their results. |

- **Keys look like `INC-017`:** letters, a dash and a number, the form the agent's `get_incident` tool looks up. `inc-17` is accepted as `INC-17`.
- **Every change also updates the search index**, in the same transaction, so `search_previous_incidents` finds exactly the incidents listed here. The text is the same as for an ingested incident (`index_incident()` in `app/rag/ingest.py`).
  - Indexing embeds the text: one small call to the embedding model. It's skipped when the searchable text (key, title, severity, date, symptoms, root cause, fix) didn't change, so a change of status or tags costs nothing.
  - If the model can't be reached, nothing is saved, and the answer is a 502 with a readable `detail`.
- A time without a timezone is read as UTC.

## Investigations API

`app/api/routes/investigations.py`. Every route needs a login, and `{investigation_id}` routes give the same 404 for someone else's investigation as for a missing one.

| Route | Does |
|---|---|
| `POST /projects/{id}/investigations` | `{question, ref?, reported_at?}`. Creates the investigation and runs the agent in the background, returning 202 at once. 409 if the project has no indexed repository, 429 when a guard rail is hit. `ref` is the deployed version (default: the default branch). |
| `GET /projects/{id}/investigations` | The project's investigations, newest first. |
| `GET /investigations/{id}` | The row: status, root cause, confidence, tokens, cost, and `report` (an `InvestigationReport`: the `report` event's payload plus `deployed_ref`). |
| `GET /investigations/{id}/events?after=` | Saved events after `seq`, as a JSON list of `Event`s, for rendering a finished investigation. |
| `GET /investigations/{id}/stream` | SSE. Replays saved events after `Last-Event-ID` (or `?after=`), then polls for new ones every 0.5 s. A keep-alive comment is sent every 15 s, and the stream closes after `done`. |
| `POST /investigations/{id}/messages` | A follow-up question. The answer comes back in the response, cites evidence labels like `[F2]`, and is also published as a `message` event. 409 before the report exists. |
| `POST /investigations/{id}/approve` | The engineer's approval: opens the verified fix as a draft PR and returns `{url}`. 409 if there's no fix, 503 without `GITHUB_TOKEN`. |

**Guard rails for the public demo** (settings in `.env`): one running investigation per user, `INVESTIGATIONS_PER_HOUR` per user, `DAILY_BUDGET_USD` across everyone, and `INVESTIGATION_COST_CAP_USD` per run. A run past its cap stops. A run older than 30 minutes no longer counts as running, so a server restart can't lock anyone out.

The request and response models live in `app/schemas/investigation.py`. `/events` and `/stream` both declare the event union from `app/schemas/events.py`, so the OpenAPI schema has a type for every event the page renders.

The MCP servers are mounted at `/mcp/github`, `/mcp/docs` and `/mcp/incidents`. See the root README for connecting a client.

## Chat

`POST /chat` (`app/api/routes/chat.py`) is a conversation with Nemotron: `{messages, tier, project_id?}` in, the answer plus `model`, `tier`, tokens, cost, latency and `citations` out.

- **With a `project_id`** (one of the user's projects, else 404), the answer comes from that project's knowledge:
  - The question is searched with the same hybrid search the agent uses (`app/rag/retrieve.py`).
  - The best 5 chunks go to the model in a system message, as sources labelled `[S1]`…`[S5]`, and it's told to cite the ones it uses.
  - `citations` lists only the sources the answer cites in square brackets, in label order. Each has its `label`, `source_type`, `path`, a readable `ref` (`docs/pool.md § Pool @ f89f6df`) and a `source_ref` for the source viewer.
  - When nothing matches, the model is told so, and to say that its answer is general knowledge.
- Without one, it's a plain conversation, and `citations` is empty.
- The reported cost is the answer's; the search adds one embedding call, plus a Nano rerank when there are more than 5 candidates.

- It needs a login. Each user may send `CHAT_MESSAGES_PER_HOUR` messages (default 30), then gets 429 until the hour has passed. The counts are kept in the process, so a restart resets them.
- `tier` is `nano` (default) or `super`. Ultra is refused (422), because it's the expensive tier. Answers stop at 512 tokens, and a request holds at most 20 messages.
- If the provider still fails after the router's retries, the answer is a 502 with a readable `detail`. The server log keeps the reason (for example an invalid key), never the conversation.

## API surface

Every route in [IMPLEMENTATION_PLAN.md §11](../docs/IMPLEMENTATION_PLAN.md#11-backend-api-surface-fastapi), plus the ones the backlog adds, exists with its final request and response models. So `/openapi.json` (and `/docs`) is the whole contract, and the frontend types are generated from it. A route that isn't built yet answers, after the login, ownership and validation checks:

```json
501 {"detail": "Not implemented yet (B-401)", "code": "not_implemented"}
```

The task id is the backlog item that fills it in. To build one, replace its `raise not_implemented(...)` with the real body, remove its row from `STUBS` in `tests/test_api_surface.py`, and add the route's own tests.

After any change to a route or a request/response/event model, run `npm run gen:api` in `frontend/` and commit `frontend/openapi.json` and `frontend/src/api/schema.ts` with it. CI fails if they're out of date. `scripts/export_openapi.py` writes the schema without a server or database.

| Area | Routes | Status |
|---|---|---|
| Auth | `POST /auth/register`, `/login`, `/refresh`, `/logout`, `GET /auth/me` | done |
| | `POST /auth/demo` | B-1001 |
| Projects | `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}` | done |
| Investigations | `POST/GET /projects/{id}/investigations`, `GET /investigations/{id}`, `/events`, `/stream`, `POST …/messages`, `POST …/approve` | done (see above) |
| | `GET /investigations/{id}/messages` | B-701 |
| | `POST /investigations/{id}/patches/{patch_id}/rerun` | B-604 |
| | `GET /investigations/{id}/trace` | B-803 |
| Repositories | `GET/POST /projects/{id}/repositories`, `PATCH/DELETE …/{repository_id}` | done (see above) |
| Knowledge | `POST /projects/{id}/ingest`, `GET /projects/{id}/knowledge?type=&q=`, `GET /projects/{id}/logs`, `/logs/facets`, `POST /projects/{id}/uploads`, `/uploads/preview` | done (see above) |
| | `GET /projects/{id}/sources?ref=` | B-401 |
| Incidents | `GET/POST /projects/{id}/incidents`, `GET/PATCH/DELETE …/{incident_id}` | done (see above) |
| Memories | `GET /projects/{id}/memories?category=&status=`, `PATCH/DELETE …/{memory_id}` | B-703 |
| Evaluation | `GET /evaluation/runs`, `GET /evaluation/runs/{id}` | B-901 |
| Dashboard | `GET /stats/dashboard?project_id=` | B-804 |
| Chat | `POST /chat` | done |

- `/investigations/{investigation_id}` routes take `investigation: OwnedInvestigation` from `app/api/deps.py`. It's the same rule as `OwnedProject`: someone else's investigation gives the same 404 as a missing one.
- An evaluation run's `metrics` and `cases` stay loose JSON until `evaluate.py`'s output shape is agreed.

## Investigation events (v1, agreed)

The agent publishes payloads from `app/schemas/events.py`. `make_event()` wraps each one in an envelope `{seq, ts, type, payload}`, which is stored in `investigation_events` and streamed over SSE as `id: <seq>`, `event: <type>`, `data: <json>`. Types marked * are additions to the plan's list.

| Event | Sent when | Key payload fields |
|---|---|---|
| `status`* | investigation status changes | `status` (queued, running, awaiting_approval, completed, failed) |
| `step_started` | a graph node starts | `step`, `round`, `tier`, `model` |
| `step_completed` | a node ends or is skipped | `step`, `round`, `duration_ms`, `summary`, `skipped` |
| `tool_call` / `tool_result` | each tool call | `call_id` pairs them; `tool`, `arguments`, `transport` / `ok`, `summary`, `output_preview` |
| `evidence_added` | a fact or inference is recorded | `evidence_id`, `label` (F1, I1), `kind`, `statement`, `source_ref`, `excerpt`, `cites` |
| `hypothesis` | a root cause is proposed | `hypothesis_id`, `statement`, `rank`, `round` |
| `verdict` | the verifier decides | `verdict`, `notes`, `gaps`, `reinvestigating` |
| `confidence` | after scoring, and again after the fix is verified | `score`, `components` (name, value, weight), `contradiction_ids` |
| `patch_proposed` | a patch is generated | `patch_id`, `attempt`, `diff`, `files` (path, explanation) |
| `test_output` | each sandbox output line | `patch_id`, `phase` (baseline or patched), `line` |
| `test_completed`* | a sandbox run ends | `exit_code`, `passed`, `failed`, `duration_ms`, `sandbox_image_id`, `as_expected` |
| `patch_status`* | verify_fix decides | `status` (verified, tests_failed, untested, rejected), `notes` |
| `pr_draft`* / `pr_opened`* | PR text is ready / the draft PR was created | `title`, `body` / `url` |
| `report` | the final report | `root_cause`, `summary` (cites `[F1]`), `verdict`, `fact_ids`, `fix` (verified, untested, failed, none) |
| `message_delta`* / `message`* | a follow-up chat reply streams / finishes. Only `message` is sent today: replies arrive whole. | `message_id`, `delta` / `content`, `evidence_ids` |
| `error` | something went wrong | `message`, `step`, `fatal` |
| `done` | the run ends; the stream closes | `status`, `duration_ms`, `total_tokens`, `total_cost_usd` |

- `step` is one of the graph nodes in `Step`, in run order. The agent runs 14 of the 16: `await_approval` and `create_pr` aren't graph steps, because approval is the separate `/approve` call, which sends `status` and `pr_opened`. The timeline shows the 14 from the start.
- The stream's `data:` is the whole envelope. `GET /events` returns the same envelopes as a JSON list.
- Payloads reject unknown fields, and a fact without `source_type`, `source_ref` and `excerpt` is rejected at publish time.
- `tests/test_events.py` pins the set of event types and the wire format. Changing the contract means updating that test, regenerating the frontend types, and telling the other person.

## Errors and CORS

Every error response, from any route, has this shape (`app/core/errors.py`):

```json
{"detail": "Project not found", "code": "not_found"}
{"detail": "Request validation failed", "code": "validation_error",
 "errors": [{"field": "body.email", "message": "value is not a valid email address: ..."}]}
```

- `detail` is always a readable string the UI can show. `code` comes from the status: `unauthorized`, `not_found`, `conflict`, `method_not_allowed`, `validation_error`, `internal_error`, and so on.
- Raise `HTTPException(status, "message")` as usual. Its headers, such as `WWW-Authenticate`, are kept.
- Validation errors never echo the submitted values, so a rejected password can't leak into a response.
- Unhandled exceptions return a generic 500. The traceback goes to the server log only.
- CORS allows only `FRONTEND_ORIGIN`, with credentials (for the refresh cookie), the methods GET, POST, PATCH and DELETE, and the headers `Authorization`, `Content-Type` and `Last-Event-ID`.

## Security helpers

`app/core/security.py`:

- **Passwords:** `await hash_password(pw)` and `await verify_password(hash, pw)` use argon2id (about 65 ms each, run in a worker thread). `verify_password` returns `False` rather than raising.
- **Tokens:** `create_access_token(user_id)` and `create_refresh_token(user_id)` make HS256 JWTs. `decode_token(token, TokenType.ACCESS)` checks the signature, expiry and token type, and raises `InvalidTokenError` on any failure. A refresh token is never accepted as an access token.
- **Secrets at rest:** `encrypt_secret(text)` and `decrypt_secret(ciphertext)` use Fernet, for GitHub tokens and similar. Decryption raises `DecryptionError` if the value was tampered with or the key changed.
- If `JWT_SECRET` or `FERNET_KEY` is unset, development and tests use built-in dev-only values and log a warning. Production refuses to start without them, rejects a `JWT_SECRET` shorter than 32 characters, and rejects a malformed `FERNET_KEY`. Changing `FERNET_KEY` makes stored tokens unreadable.

## Models and migrations

Tables live in `app/models/`. After changing a model:

```bash
uv run alembic revision --autogenerate -m "add evidence table"   # then read the generated file
uv run alembic upgrade head
uv run alembic downgrade -1                                       # undo the last migration
```

- Import every new model in `app/models/__init__.py`, or Alembic won't see it.
- Inherit `UUIDPrimaryKeyMixin` and `CreatedAtMixin` from `app/models/base.py` for the `id` and `created_at` columns.
- For status-like columns, use a `StrEnum` with `enum_type()` for the column and `enum_check()` in `__table_args__`. Adding a value later needs a migration that replaces the CHECK constraint, because autogenerate doesn't detect CHECK changes.
- Autogenerate only compares tables defined in `app/models/`, so LangGraph's checkpoint tables and the `demo_app` schema are left alone.
- Embedding columns are `Vector(EMBEDDING_DIMENSIONS)`: 1024, which must equal `EMBED_DIMENSIONS`. Each has an HNSW cosine index. Changing the size needs a migration that alters the columns and rebuilds those indexes.
- `document_chunks.tsv` is a generated column (Postgres fills it from symbol + heading path + content), so ingestion never writes it.
- `tests/test_migrations.py` fails if a model changed without a migration, and checks that every migration downgrades and upgrades cleanly.
- Tests run against `<db>_test`, which is dropped and recreated on every run. The `db_session` fixture rolls back each test's changes.
