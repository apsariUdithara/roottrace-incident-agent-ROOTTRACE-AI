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
uv run alembic upgrade head               # create or update the tables
uv run uvicorn app.main:app --reload      # http://localhost:8000/health
uv run pytest                             # needs Postgres running; uses a separate <db>_test database
uv run ruff check . && uv run ruff format --check .
```

`GET /health` says the process is up. `GET /health/ready` also checks the database and returns 503 if it can't be reached.

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
