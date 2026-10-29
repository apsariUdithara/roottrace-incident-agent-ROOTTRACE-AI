# frontend

Vite + React + TypeScript app: Tailwind v4 + shadcn/ui (Base UI), TanStack Query, React Router v7 (data mode), Monaco diff, Recharts. See [IMPLEMENTATION_PLAN.md §12](../docs/IMPLEMENTATION_PLAN.md#12-frontend-frontend).

Pages: Login/Register + "Try demo", Dashboard, Investigation, Knowledge, Evaluation.

API and SSE event types are generated from the backend's OpenAPI schema, never written by hand.

Owner: Person B.

## Run locally

Start the backend first (see [backend/README.md](../backend/README.md)). Then, from `frontend/`:

```bash
npm install
npm run dev       # http://localhost:5173 — /api/* is proxied to the backend on :8000
npm run lint
npm test          # vitest, once (npx vitest to watch)
npm run build     # type-check + production build into dist/
```

## Calling the API

Use `api` and `ok` from `src/api/client.ts`. Paths, parameters, bodies and responses are all typed from the generated schema:

```ts
import { api, ok } from '@/api/client'

useQuery({ queryKey: ['projects'], queryFn: () => ok(api.GET('/projects')) })

const investigation = await ok(
  api.POST('/projects/{project_id}/investigations', {
    params: { path: { project_id } },
    body: { question },
  }),
)
```

- `ok()` returns the data, or throws an `ApiError` with `status`, `message` (the backend's readable `detail`), `code` and `fieldErrors` (for a 422).
- **Login:** the access token is kept in memory only (`src/api/session.ts`), never in localStorage. Every request sends it as `Authorization: Bearer …`.
- **Expired token:** on a 401, the client calls `POST /auth/refresh` once (the browser sends the httpOnly refresh cookie) and retries with the new token. Requests that fail at the same moment share one refresh. A reload restores the session the same way.
- **Session ended:** if the refresh fails, the session is cleared and the caller gets the 401. Subscribe with `useSyncExternalStore(subscribeSession, getSession)` to react, e.g. by sending the user to the login page.
- A 401 from `/auth/login` or `/auth/register` means a wrong password or a taken email, and never triggers a refresh.
- Queries retry once on network and 5xx errors, never on a 4xx (`shouldRetry` in `src/lib/query-client.ts`).

## Login and protected pages

- `/login` and `/register` log in and go on to `?next=` (or home). `safeNext()` only follows a `next` that is a page of this app, so a link can't use the login page to send someone to another site.
- **Every page that needs a login goes under the `ProtectedLayout` route** in `src/routes.ts`, inside `AppShell`. Login, register and not-found use `AuthLayout`, a plain header with the name.
  - On arrival, its loader (`requireSession`) checks for a session. After a reload there isn't one in memory, so it tries the refresh cookie first, showing "Loading…" meanwhile. Otherwise it sends the visitor to `/login?next=<this page>`.
  - After that, the layout follows the session. When it ends, by logging out or because the refresh cookie has expired, the login page shows straight away, with the way back.
- **Log out** is in the header. It clears the refresh cookie on the server, ends the session here even if the server can't be reached, and drops all cached query data, since another user may log in next.
- `useSession()` (`src/lib/auth.ts`) gives any component the current user, or null.

## App shell

Logged-in pages render inside `AppShell` (`src/layouts/app-shell.tsx`):

- **Sidebar** (`src/components/app-sidebar.tsx`): Dashboard, Projects, Knowledge, Logs, Evaluation and Chat. The current section is highlighted and marked `aria-current="page"`. It collapses to icons with the top bar's button or Ctrl/⌘+B, and becomes a slide-over drawer below 768 px.
- **Top bar:** the project switcher, the user's email, and Log out.
- **Project switcher** (`src/components/project-switcher.tsx`): the user's projects, from `GET /projects`. A page about one project reads the selection with `useCurrentProject()` (`src/lib/current-project.ts`).
  - The choice is remembered in this browser. If it's missing, blocked, or isn't one of this user's projects, the switcher uses the first project.
  - With no projects, the switcher becomes a link to create one.
- **To add a page:** add a route under `AppShell` in `src/routes.ts`, and a `NAV` entry in `app-sidebar.tsx` if it belongs in the sidebar. Start the page with `PageHeader` (`src/components/page-header.tsx`).
- Dashboard and Evaluation are placeholders for now (`PlannedPage`). Each one says which backlog task builds it, like the backend's 501 stubs.
- **To keep a heavy page out of the first download**, give its route `lazy` instead of `Component` (as `/chat` does). Its code then loads when it's first opened.

## Projects

- **`/projects`** (`src/pages/projects-page.tsx`) lists the user's projects, newest first, and marks the current one. Its form creates a project, makes it the current one, and opens its settings.
- **`/projects/:projectId`** (`src/pages/project-settings-page.tsx`) edits the name and description, and holds the repository (`src/components/repository-settings.tsx`):
  - With none, a form connects a GitHub repository: URL, default branch, an optional access token, the setup command (empty: auto-detect) and the test command.
  - Once connected, it shows whether the tests can run in the sandbox, and the indexing status. The same settings can be changed there.
  - **The access token is write-only:** the field is a password field, it's never filled in, and it's emptied once saved. Leaving it empty keeps the saved token; Remove token deletes it.
  - Disconnect asks first, because the repository's docs and code leave the knowledge base with it.
- A project has one repository, because the agent investigates one codebase (a second gives 409).

## Knowledge

`/knowledge` (`src/pages/knowledge-page.tsx`) is what the agent searches in the current project.

- **The repository card** shows its indexing status, and Index now / Re-index (`POST /projects/{id}/ingest`). The button is disabled while a run is going.
  - While a run is queued or running, the repository query polls every 2 s (`useRepositories` in `src/lib/repositories.ts`). The project settings use the same query, so they follow the run too.
  - A failed run shows the backend's reason, and which commit the agent still searches.
  - When a run ends, the list reloads by itself: `last_indexed_at` is part of its query key.
- **Tabs:** Docs, Code, Issues and Incidents, with a search of titles and paths. It searches once typing pauses (300 ms), not on every key. 50 items a page.
- The tab, search and page are in the URL (`?type=code&q=pool&offset=50`), so a view can be shared or reloaded. `src/lib/search-params.ts` has the helpers: `withChanges` for the URL, and `useDebouncedParam` for a search box.
- An item opens in the source viewer (see below), by its `source_ref`.
- `IngestBadge` (`src/components/ingest-status.tsx`) shows an indexing status anywhere.

## Logs

`/logs` (`src/pages/logs-page.tsx`) is the current project's structured logs, newest first: the lines the agent's `search_logs` tool reads.

- **Filters:** level, service (only when there's more than one) and error type, from the values the logs hold (`GET /projects/{id}/logs/facets`, with counts); route (suggestions from the logs) and message text, once typing pauses; and a From/To window. They match as in the agent's tool, so the same filters find the same lines as its evidence.
- **Times are UTC**, in the table and the window, as in the agent's evidence.
- **A trace id** shows every line of that trace, whatever else was filtered.
- The filters and page are in the URL (`?level=ERROR&route=/payments&since=2026-10-06T14:20:00Z`), so a link can open the lines behind a finding. 100 lines a page.
- Change the URL's filters with `useSearchParamsUpdate()` (`src/lib/search-params.ts`), not `setSearchParams`: React Router's functional update starts from the last render's params, so two quick changes (a filter, then the debounced search) could undo each other. The hook chains them.
- A window whose From is after its To is pointed out on the page, without asking the backend.

## Upload

`/upload` (`src/pages/upload-page.tsx`) brings logs or incident history into the current project. The Logs page and the Knowledge page's Incidents tab link to it (`?kind=logs`, `?kind=incidents`).

1. **Choose** what the file holds and the file. It's checked at once (`POST /projects/{id}/uploads/preview`), which saves nothing.
2. **Match** its columns to our fields. The backend guesses first; each field shows its value in the file's first row. Changing a column checks the file again.
3. **See** what importing would do: how many rows will be imported, how many the project already has, and the rows with a problem (the first 20).
4. **Import** sends the same file with the mapping that was checked. Afterwards the logs and knowledge lists reload.

## Chat

`/chat` (`src/pages/chat-page.tsx`) talks to Nemotron through `POST /chat`.

- Each request sends the conversation so far, at most the latest 20 messages, starting with a question (`historyForRequest` in `src/lib/chat.ts`). The Model picker chooses Nano (fast) or Super (stronger).
- **Search <project>** (on by default, when there is a current project) sends its `project_id`: the answer then comes from the project's docs, code, issues and past incidents. The sources it cites appear under it as chips (`CitationChip`, `src/components/source-chip.tsx`), and the answer refers to them inline as [S1], [S2]. A chip opens its source in the viewer, in the project that answer searched, even after switching projects.
- Under each answer: the model that answered, the time it took, the tokens and the cost.
- Enter sends and Shift+Enter starts a new line. An empty chat offers example questions, and New chat starts over.
- On an error, the backend's own message is shown (for example the hourly limit), with Try again.
- **Markdown** (`src/components/markdown.tsx`, GitHub-flavoured) renders model output. Raw HTML in it is never rendered, because model output is untrusted, and links open in a new tab. Reuse it for any text from the model.

## Sources: chips and the viewer

Every claim the app shows can be opened at its source: the code lines, commit, doc section, log lines, incident, issue or memory behind it.

- **Refs come in two forms**, and `describeRef()` (`src/lib/source-refs.ts`) reads both, as the backend's `GET /projects/{id}/sources` does: the app's (`file:app/db.py#L3-L9@<sha>`, `doc:docs/pool.md`, `incident:INC-017`, `issue:42`) and the agent's, which are its tools' refs (`src/config.py L1-12 @ v1.4.8`, `docs/database.md § Database > Connection pooling L5-17 @ 6c4a9a3`, `commit 5b0bb55c90`, `INC-017`, `log trace_id=…`, `log deploy at …`, `sql: …`). It gives the kind and a short form for a chip.
- **Chips** (`src/components/source-chip.tsx`): `SourceChip` shows an icon for each of the 8 evidence source types, the label (F1, S1) and the short form, with the whole ref as its tooltip. Give it `onOpen` and it's a button. A query result (`sql: …`) is never one: results aren't kept. `EvidenceChip` takes an `evidence_added` payload (a fact by its source, an inference by its label); `CitationChip` a chat citation, opening the search's own ref, which names the exact section or lines.
- **The viewer** is one drawer in the app shell (`src/components/source-viewer.tsx`). Open a ref from any page with `const openSource = useSourceViewer()` (`src/lib/source-viewer.ts`), then `openSource(projectId, ref)`. It shows the backend's reason when a ref doesn't open.
  - Views for each kind are in `src/components/source-views.tsx`. Files show line numbers with the cited lines marked and scrolled to; commits their message, files and a coloured diff; docs and issue bodies render as Markdown, loaded lazily (`doc-markdown.tsx`), scrolled to the cited heading (`headingSlug()` matches the backend's slug). A trace's log lines link to `/logs?trace_id=…`.
  - No syntax highlighting: the cited lines are what matter.
- `LevelBadge` (`src/components/log-level.tsx`) and `formatUtc` (`src/lib/time.ts`) are shared by the Logs page and the viewer.

## Docker

`frontend/Dockerfile` builds the app and serves it with Caddy, which also proxies `/api` to the backend. See [infra/README.md](../infra/README.md#docker-images).

## Tests

`npm test` runs vitest. Tests run on Node by default. A test of components starts with `// @vitest-environment jsdom`. It mounts the real `routes` in a memory router (`renderApp`) and fakes the backend by stubbing `fetch` (`backend()`); both come from `src/test/helpers.tsx`. Call `stubMatchMedia()` first, because jsdom has no `matchMedia` and the sidebar uses it. `backend()` knows login, projects and chat; give it `handle` to answer other routes, with the parsed body (an upload's `FormData`) and the query string (see `fakeBackend` in `projects-page.test.tsx`). A test that uploads a file stubs the globals `FormData` and `File` with Node's own (`NodeFormData`, `NodeFile` from `src/test/node-uploads.ts`): Node's fetch can't send jsdom's, and newer Node versions send an empty part instead (`upload-page.test.tsx`). For polling, use `vi.useFakeTimers({ shouldAdvanceTime: true })` and advance by `POLL_MS` (as in `knowledge-page.test.tsx`).

`src/test/fixtures/scenario-1-events.json` is a whole investigation's events (scenario 1, from `backend/scripts/scenario1_replay.py`), as `GET /investigations/{id}/events` returns them. Use it to test the Investigation page. Don't edit it: regenerate it with `uv run python scripts/seed_fake_investigation.py --json` in `backend/`.

## API types

The types for every route, request, response and investigation event are generated from the backend, so a backend change can't silently break the UI:

```bash
npm run gen:api     # backend → openapi.json → src/api/schema.ts (needs uv; no server or database)
npm run gen:types   # just openapi.json → src/api/schema.ts
```

- `openapi.json` is the contract: the backend's OpenAPI schema, written by `backend/scripts/export_openapi.py`.
- `src/api/schema.ts` is generated by `openapi-typescript`. Never edit it.
- `src/api/types.ts` gives the generated types short names. `InvestigationEvent` is the union of all event types: an item of `GET /investigations/{id}/events`, and the `data` of each `/stream` frame. Narrow it on `type`, or pick one type with `EventOf<'evidence_added'>`.
- After changing a backend route or model, run `npm run gen:api` and commit both files in the same PR. CI fails if either is out of date.
- `openapi-typescript` 7 only declares TypeScript 5 as a peer. The `overrides` entry in `package.json` makes it use this project's TypeScript 6, which it works with.

## Conventions

- **Forms:** wrap each control in `FormField` (`src/components/form-field.tsx`) for its label and hint. Show a failed request with `errorMessage()` (`src/lib/errors.ts`): the backend's own words, or the field errors of a 422.

- Import from `src/` with the `@/` alias, e.g. `import { Button } from '@/components/ui/button'`.
- Add shadcn components with `npx shadcn@latest add <name>`. They land in `src/components/ui/`.
- Routes live in `src/routes.ts` (`src/router.ts` only creates the browser router), pages in `src/pages/`, layouts in `src/layouts/`.
- The app always runs in dark mode (`class="dark"` on `<html>`).
