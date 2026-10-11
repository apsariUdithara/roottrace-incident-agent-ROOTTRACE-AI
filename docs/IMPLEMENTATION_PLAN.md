# RootTrace AI — Implementation Plan (AI Engineering Incident Investigator)

## Context

We are entering the **Nebius × NVIDIA Global AI Hackathon** (submission window closes **30 Oct 2026, 10:00 PT**). The concept is in [docs/ai-incident-investigator-hackathon-plan.pdf](docs/ai-incident-investigator-hackathon-plan.pdf): an agent that investigates a production incident across code, commits, docs, logs, issues and past incidents. It produces an evidence-backed root cause with a confidence score, then **writes, tests and verifies a code patch** and drafts a PR.

The repo is empty apart from that PDF (greenfield). Team: **2 people**. Agent framework: **LangGraph**. Demo target: a **purpose-built "PayStream" repo with seeded bugs**.

**Hard rules from the hackathon (verified):**
- Must run on **Nebius Token Factory or Nebius AI Cloud**.
- Must use **≥1 NVIDIA open model**.
- Code must be public and open-source licensed.
- Required for submission: a hosted demo URL, a YouTube demo video, a project description, and a feedback note on Nebius/NVIDIA.
- The primary track, *Coding & Agentic Engineering*, is explicitly about agents that "write, run, and test code in **Token Factory Sandboxes**". So `run_tests()` must run on the **Nebius Sandboxes (ConTree) API**, not on local Docker. This is the biggest change from the PDF.
- Judging has four equally weighted criteria: Technological Implementation, Design, Potential Impact, Quality of Idea.

**Intended outcome:** a deployed app where a judge clicks **"Try demo"**, describes *"Payment API returning 500s after yesterday's deploy"*, and watches the agent do the following live:
1. Plan the investigation.
2. Call tools and collect evidence.
3. Hypothesize, then verify.
4. Score its confidence.
5. Generate a patch.
6. Show a **red → green** test run in a Nebius Sandbox.
7. Draft a PR.

The full run should take **under about 2 minutes**.

---

## 1. Final tech stack (decisions made)

| Layer | Choice | Why |
|---|---|---|
| LLM | Nemotron 3 **Ultra / Super / Nano** via Nebius Token Factory, OpenAI-compatible API (`https://api.tokenfactory.nebius.com/v1/`) | Mandatory rule; the three-tier router is a judging talking point |
| Agent | **LangGraph** `StateGraph` + `AsyncPostgresSaver` checkpointer | Native loops (Verifier → Investigator), interrupts (human approval), streaming, and short-term memory for free |
| Backend | Python 3.12, **FastAPI**, SQLAlchemy 2 (async) + Alembic, Pydantic v2, `uv` | Matches the PDF |
| DB | **PostgreSQL 16 + pgvector** (one DB for app data, vectors, memory, traces, checkpoints) | One system to run |
| Sandbox | **Nebius Token Factory Sandboxes** (`contree_sdk`), behind a `SandboxRunner` interface with a local Docker fallback for development only | Track requirement |
| MCP | Python `mcp` SDK (**FastMCP**), streamable HTTP mounted in FastAPI. Agent consumes it via `langchain-mcp-adapters` | Standard protocol, reusable from Claude Desktop/Cursor |
| Frontend | Vite + React + TypeScript, Tailwind + shadcn/ui, TanStack Query, React Router, `@microsoft/fetch-event-source` (SSE with auth header), Monaco `DiffEditor`, Recharts | Fast to build, polished look |
| Observability | Own `trace_spans` table + Trace waterfall tab in the UI. Optional Langfuse export later | No extra service; visible to judges |
| Deploy | Docker Compose on a **Nebius AI Cloud** Compute VM (Caddy for TLS) + Nebius Container Registry. GitHub Actions CI/CD | Everything on Nebius |
| License | **Apache-2.0** | Required |

**Model IDs.** Keep these in env vars. Confirmed against `GET /v1/models` on 2026-10-11; IDs are case-sensitive.
- `NEMOTRON_ULTRA=nvidia/Nemotron-3-Ultra-550b-a55b`
- `NEMOTRON_SUPER=nvidia/nemotron-3-super-120b-a12b` 
- `NEMOTRON_NANO=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`
- `EMBED_MODEL=Qwen/Qwen3-Embedding-8B` with `dimensions=1024` (confirmed: default is 4096, and the API honours a 1024 request). Column type is `vector(1024)`, which fits the HNSW index.

**Embedding size limit.** pgvector's HNSW index supports at most 2000 dims (`vector`) or 4000 dims (`halfvec`). If the embedding model is larger, either request a smaller `dimensions` value (MRL) or skip the index. The demo corpus is small, so an exact scan is fine.

---

## 2. Repository layout (monorepo)

```
RootTrace-AI/
├─ backend/
│  ├─ app/
│  │  ├─ main.py                  # FastAPI app, routers, MCP mounts
│  │  ├─ core/                    # config.py (pydantic-settings), db.py, security.py (JWT, argon2, Fernet)
│  │  ├─ models/                  # SQLAlchemy tables (section 4)
│  │  ├─ schemas/                 # Pydantic API + agent I/O schemas (Evidence, Hypothesis, Report, Patch…)
│  │  ├─ api/routes/              # auth, projects, repositories, knowledge, incidents, investigations, memories, evaluation
│  │  ├─ llm/                     # router.py (tiered Nemotron client), structured.py (JSON-schema + retry), pricing.py
│  │  ├─ prompts/                 # versioned prompt files: planner.v1.md, hypothesis.v1.md, …
│  │  ├─ rag/                     # ingest.py, chunkers/ (markdown, python_ast, incident), embed.py, retrieve.py (hybrid+RRF), rerank.py
│  │  ├─ tools/                   # registry.py + one module per tool (section 6)
│  │  ├─ agent/                   # state.py, graph.py, nodes/*.py, confidence.py, events.py
│  │  ├─ memory/                  # long_term.py (short-term = LangGraph checkpointer)
│  │  ├─ sandbox/                 # base.py (SandboxRunner), nebius.py (ConTree), docker_local.py (dev fallback)
│  │  ├─ mcp_servers/             # github_mcp.py, docs_mcp.py, incident_mcp.py (wrap tools/registry)
│  │  └─ observability/           # tracing.py (span context manager, token/cost capture)
│  ├─ alembic/  tests/  pyproject.toml  Dockerfile
├─ frontend/                      # Vite React app (section 9)
├─ demo-target/paystream/         # seeded target service (section 3) — also pushed as its own public GitHub repo
├─ eval/                          # cases.yaml, hidden_tests/, evaluate.py, results/
├─ infra/                         # docker-compose.yml, docker-compose.prod.yml, Caddyfile, sandbox image Dockerfile
├─ .github/workflows/             # ci.yml, deploy.yml
├─ docs/                          # PDF, architecture diagram, this plan, FEEDBACK.md
├─ README.md  LICENSE
```

---

## 3. Demo target: "PayStream" (build this FIRST — it is the ground truth for demo + eval)

This is a small FastAPI payments service with `src/{auth,payments,users,database}`, a pytest suite, and docs:
`docs/{architecture,database,authentication,payments,deployment,api-reference}.md`.

A script, `demo-target/scripts/build_history.py`, builds a **realistic git history**. It has about 40 normal commits, and each scenario below gets one "bad" commit with a plausible message and author/date. Each scenario ships a complete evidence bundle:

| # | Incident | Seeded bug | Evidence the agent should find | Fix |
|---|---|---|---|---|
| 1 | Payment API 500s after deploy *(hero demo)* | `DB_POOL_SIZE` 20→100 > Postgres `max_connections` | Timeout and "too many connections" errors in logs; the commit; `database.md` "recommended pool ≤ 20"; past incident INC-017 | Restore the limit and add validation |
| 2 | Wrong totals on multi-item orders | `float` replaced `Decimal` in `calculate_total` | Rounding-diff logs, commit, `payments.md` | Back to `Decimal` + quantize |
| 3 | Users logged out early / 401s | Naive vs aware `datetime` in token expiry | 401 spike, commit, `authentication.md` | Timezone-aware comparison |
| 4 | Duplicate charges | Idempotency-key check removed | Duplicate charge IDs in logs, GitHub issue, past incident INC-009 | Restore the check |
| 5 | Webhook handler 500s | `None` access on an optional field | Stack trace in logs | Null guard |
| 6 | Gateway retry storm | Exponential backoff removed | Burst logs, commit | Restore backoff |
| 7 | Config not loaded after rename | Env var renamed in code, not in deploy config | KeyError logs, `deployment.md` | Fix the name |
| 8 | Slow user search (timeouts) | Index dropped in a migration | Slow-query logs | Re-add the index (tests verify the migration) |

The rest of the fixtures:
- **Logs:** `fixtures/logs/*.jsonl` (structured: ts, level, service, route, status, latency, error, trace_id), with noise mixed in.
- **Incidents:** `fixtures/incidents/INC-0xx.yaml` (symptoms, root cause, fix, commit). About 15 records, including near-misses that test retrieval.
- **Issues:** `fixtures/issues/*.json`, also created as real issues on the public GitHub repo.
- **Regression tests:** each scenario has one. The agent **never sees** these tests (they live in `eval/hidden_tests/`). Visible tests stay in the repo.

**Second demo project (generalization):** pick one small, real open-source Python repo with a clean `pip install` and a fast pytest suite. Find a real past bug-fix commit in it, check out the parent commit, and write the incident from the original issue. The agent investigates it with no special handling. It is used for 2–4 eval cases and a short segment of the video. A cited root cause is the goal there, and a tested patch is a bonus.

---

## 4. Database schema (PostgreSQL + pgvector, Alembic)

Every row below `projects` carries a `project_id`. All queries are scoped through `project.owner_id == current_user.id`.

- `users`: id, email, password_hash (argon2), created_at
- `projects`: id, owner_id, name, description
- `repositories`: id, project_id, provider, url, default_branch, local_path, encrypted_token, last_indexed_sha, `setup_command` (default: auto-detected `pip install -r requirements.txt` or `pip install -e .`), `test_command` (default: `pytest -q`), `sandbox_status` (unknown|ready|untestable)
- `documents`: id, project_id, source_type (doc|code|issue|incident), path, title, commit_sha, hash
- `document_chunks`: id, document_id, project_id, content, section_path, start_line, end_line, symbol, `embedding vector(N)`, `tsv tsvector` (GIN index), metadata jsonb
- `incidents`: id, project_id, key (INC-017), title, symptoms, root_cause, fix, commit_sha, status, occurred_at
- `log_entries`: id, project_id, ts, level, service, route, status, message, error_type, trace_id, attrs jsonb. Indexed on (project_id, ts) and error_type. Filled from the PayStream fixtures or from user uploads (section 6).
- `investigations`: id, project_id, incident_id, user_id, question, status, root_cause, confidence, confidence_breakdown jsonb, report jsonb, started_at, finished_at, total_tokens, total_cost_usd
- `messages`: id, investigation_id, role, content, created_at (follow-up chat)
- `investigation_events`: id, investigation_id, seq, type, payload jsonb, ts. This is the SSE log, so a page reload replays everything.
- `evidence`: id, investigation_id, kind (fact|inference), source_type, source_ref (file:line / sha / INC / log trace_id / doc#section), excerpt, supports_hypothesis_id, stance (supports|contradicts|neutral), weight
- `hypotheses`: id, investigation_id, statement, rank, verdict (supported|weak|refuted), verifier_notes
- `patches`: id, investigation_id, attempt, diff, files_changed, explanation, status (proposed|tests_failed|verified|rejected), pr_title, pr_body, pr_url
- `test_runs`: id, patch_id, phase (baseline|patched), command, exit_code, stdout, stderr, passed, failed, duration_ms, sandbox_image_id
- `memories`: id, project_id, category (user_pref|project_fact|arch_decision|previous_incident|investigation_context), content, source_ref, source_investigation_id, `embedding`, created_by (agent|user), status (active|archived), updated_at
- `trace_spans`: id, investigation_id, parent_id, kind (llm|tool|mcp|retrieval|memory|sandbox), name, model, input jsonb, output jsonb, prompt_tokens, completion_tokens, cost_usd, latency_ms, status, error. This covers the PDF's `tool_calls`.
- `evaluation_runs`: id, started_at, git_sha, model_config jsonb, metrics jsonb, cases jsonb (per-case results incl. failures)
- LangGraph checkpoint tables are created by `AsyncPostgresSaver.setup()`.

A separate **read-only Postgres role** `roottrace_readonly` has SELECT on a `demo_app` schema (PayStream's simulated prod tables: payments, orders, config). The `query_database` tool uses this role.

---

## 5. Model layer — `backend/app/llm/router.py`

- `LLMRouter.complete(tier, messages, tools=None, schema=None, reasoning=bool)`. Tier is one of `ULTRA`, `SUPER` or `NANO`, and the router maps it to the model ID in env.
- It uses one `AsyncOpenAI(base_url=NEBIUS_BASE_URL)` client.
- Every call is wrapped in a `trace_span(kind="llm")` that records tokens, latency and cost. Cost comes from the `pricing.py` table of per-model $/1M tokens.
- **Structured output:** pass `response_format={"type":"json_schema",...}` when supported. Otherwise validate the JSON into a Pydantic model and retry once with the validation error appended. Either way, every agent node returns a typed object.
- **Fallback:** on 429/5xx/timeout, retry with backoff, then degrade Ultra → Super and log the downgrade on the span.
- **Reasoning toggle:** Nemotron 3 thinking on/off (verify the mechanism: system-prompt flag vs `chat_template_kwargs`). Thinking is on for Ultra hypothesis/verify and off for Nano triage.
- **Task → tier map** (config, shown in the README):
  - Nano: triage/classify, query rewrite, log summarization, reranking, memory extraction
  - Super: planning, investigator tool loop, evidence comparison, patch generation, patch explanation, PR draft, follow-up chat
  - Ultra: hypothesis, verification, verify_fix, final report. Ultra is also the escalation tier for a second patch attempt.
- Prompts are versioned files. The prompt version is stored on each span ("prompt/version management").

---

## 6. Knowledge, RAG and tools

**Ingestion** (`rag/ingest.py`, triggered from the Knowledge page or `POST /projects/{id}/ingest`):
1. Clone or pull the repo (GitPython) into `data/repos/<project>`.
2. Chunk each source type its own way:
   - Markdown by heading, with `section_path`.
   - Python by AST: function/class chunks with line ranges, using stdlib `ast`.
   - Incidents and issues: one chunk per record.
3. Embed in batches via the Nebius embeddings API. Fill `tsv`.
4. Re-index incrementally by file hash and `last_indexed_sha`.
5. Logs are **not embedded**. They load into `log_entries` and are queried structurally.
6. **Bring your own data:** `POST /projects/{id}/uploads` accepts logs (JSONL/CSV, with a column-mapping step in the UI) and incident history (YAML/JSON/CSV). Uploads go into the same `log_entries` and `incidents` tables, so the agent behaves the same on any project. Live integrations (Sentry, Datadog, Loki, PagerDuty) are roadmap items, not hackathon scope.

**Retrieval** (`rag/retrieve.py`):
1. Nano rewrites the query into 2–3 variants.
2. Hybrid search: pgvector cosine top-30 plus Postgres full-text top-30, fused with **Reciprocal Rank Fusion**.
3. **Rerank** the top 20 to the top 5 with Nemotron Nano (listwise relevance scoring). If Token Factory offers a reranker model, use that instead.
4. Return chunks with full citation metadata.

**Tools** (`tools/registry.py`). Each tool is a typed async function with a Pydantic input/output and a description. The registry produces both the OpenAI tool schemas and the MCP tool definitions, so there is a single source of truth.

| Tool | Implementation |
|---|---|
| `search_documents(query, k)` | Hybrid RAG over docs |
| `search_code(query, k)` | Hybrid RAG over code chunks + `git grep` for exact identifiers |
| `get_file(path, start, end)` | Reads from the repo at a given sha; path-traversal guard; size cap |
| `search_github_commits(query?, since?, path?)` | Local git log + diffs (GitPython); returns sha, msg, author, date, diffstat |
| `get_commit_diff(sha)` | Extra tool: the full diff of one commit |
| `search_github_issues(query)` | GitHub REST search; falls back to ingested issues |
| `search_logs(service?, level?, since?, until?, error_type?, text?)` | SQL over `log_entries`. Returns an aggregate (counts by error_type/route/time bucket) + sample lines, and Nano summarizes it |
| `get_incident(key)` / `search_previous_incidents(query)` | SQL / RAG over incidents |
| `query_database(sql)` | Read-only role. `sqlglot` parse → must be a single SELECT. Forced LIMIT 100, `statement_timeout=3s` |
| `generate_patch(root_cause, evidence_ids, files)` | Super (Ultra on retry). Outputs a **unified diff**, validated with `git apply --check` locally before the sandbox |
| `run_tests(patch_id, targets?)` | SandboxRunner (section 8) |
| `verify_fix(patch_id)` | Ultra. Checks test results + whether the diff touches the implicated files/lines + whether it addresses each evidence item |

Every tool call emits a `tool_call` and a `tool_result` SSE event and a `trace_span`. Tool outputs are truncated to a token budget before going back to the LLM.

---

## 7. Agent — LangGraph (`backend/app/agent/graph.py`)

`InvestigationState` (TypedDict) holds:
- question, incident_meta, plan[], step_status[]
- evidence[] (with IDs), hypotheses[], current_hypothesis, verifier_verdict, gaps[]
- iteration, tool_budget_left
- confidence + breakdown
- patch_attempts[], test_runs[]
- report, pr_draft
- messages

```
triage(Nano) → recall_memory → plan(Super) → investigate(Super, ReAct tool loop, ≤12 calls/round)
→ compare_evidence(Super) → hypothesize(Ultra) → verify(Ultra, may call tools to seek disconfirming evidence)
   ├─ verdict weak & iteration < 2 → investigate (targeted at `gaps`)        ← the PDF's loop
   └─ supported/refuted-final → score_confidence (deterministic)
→ fix_generator: generate_patch → run_tests(baseline+patched) → verify_fix
   ├─ fail & attempt < 2 → generate_patch (Ultra, with failure output)
   └─ else
→ write_report(Ultra) + draft_pr(Super) → save_memories(Nano) → END
→ [interrupt] human approves "Open draft PR" → create_pr (GitHub API, draft)
```

- **The Fix Generator only runs if the hypothesis verdict is `supported`.** Otherwise the report says plainly that no safe automated fix was found.
- **Investigation-only mode:** if the repo's `sandbox_status` is `untestable` (dependencies won't install, or tests need external services), the agent still returns the cited root cause and a proposed patch. The patch is clearly marked **"untested"**, and `verify_fix` checks it against the evidence only. This keeps RootTrace useful on any connected repo.
- **Repo-agnostic by rule:** no PayStream names, paths or hints in prompts, tools or agent code. A CI grep check fails the build if `paystream` appears under `backend/app/`.
- **Facts vs inferences:** the investigator records tool-observed items as `fact` (they must carry a `source_ref` and a verbatim excerpt). Compare/hypothesize nodes produce `inference` items that link to fact IDs. Any claim in the report must cite evidence IDs, and the UI renders those as clickable chips.
- **Confidence** (`agent/confidence.py`) is deterministic and explainable. It is not invented by the model. Let `w` be the weighted count of supporting facts.

  ```
  coverage      = distinct source types supporting / 4      (code, commit, logs, docs/incident)
  strength      = min(1, w / 5)
  verifier      = {supported: 1, weak: 0.4, refuted: 0}
  history       = 1 if a similar past incident matches else 0
  fix           = 1 if patch verified in sandbox else 0
  contradiction = weighted contradicting facts / total facts

  score = 0.25·coverage + 0.20·strength + 0.25·verifier + 0.10·history + 0.20·fix − 0.30·contradiction
  ```

  The result is clamped to [0.05, 0.97]. The breakdown is shown in the UI, and contradictions are listed.
- **Events:** each node emits `step_started` / `step_completed`, plus `evidence_added`, `hypothesis`, `verdict`, `patch_proposed`, `test_output` (streamed lines), `confidence`, `report`, `error` and `done`. They are persisted to `investigation_events` and streamed by `GET /investigations/{id}/stream` (SSE, with `Last-Event-ID` for resume).
- Execution: `POST /investigations` creates the row and starts the graph as a background task (`asyncio.create_task` with an in-process registry; this is fine for a single VM).
- **Follow-up chat:** `POST /investigations/{id}/messages` resumes the same LangGraph thread (checkpointer = short-term memory) in `followup` mode. A Super node gets the state plus tools. This handles "Show me the evidence", "Ignore the first commit, investigate the second", and "Apply the patch and show test output".

---

## 8. Fix Generator + Nebius Sandboxes (`backend/app/sandbox/`)

`SandboxRunner` protocol: `async prepare_base(repo) -> image_ref` and `async run(image_ref, files, cmd, timeout) -> RunResult(stdout, stderr, exit_code, duration)`.

**`NebiusSandboxRunner`** (ConTree SDK):
1. **Base image once per repo sha:** `images.use("python:3.12-slim")` (or an imported custom image built from `infra/sandbox.Dockerfile`). Then `run(shell=<repositories.setup_command>, disposable=False)` and cache the resulting image ref in the DB. This uses ConTree **branching**, so dependencies install once. If setup fails, or the baseline suite can't even be collected, set `sandbox_status=untestable` and switch to investigation-only mode (section 7).
2. **Baseline run:** upload a repo snapshot (tarball via `FilesManager.upload`) and run the target test. **We expect it to FAIL**, which proves we reproduced the bug.
3. **Patched run:** branch from the same base, upload snapshot + `fix.patch`, then run `git apply fix.patch && <test_command> <targets> && <test_command>`. **We expect a PASS.**
4. Stream stdout to the UI. Store both runs in `test_runs`. **Red → green is the demo's money shot.**
5. Secrets are never uploaded. Commands have a timeout, and network is disabled if the API supports it.
6. Optional: also register the official **Sandboxes MCP server** (`uvx contree-mcp`) as an MCP source to show interop.

The exact file-attach/run signatures are confirmed in the Phase 0 spike (`docs.tokenfactory.nebius.com/sandboxes/sdk/python_sdk/*`).

**`DockerLocalRunner`** is for offline development and CI only. Production config always uses Nebius.

**PR draft:** Super writes a title and body (root cause, evidence links, patch summary, test results). The **"Open draft PR"** button (a LangGraph interrupt plus user confirmation) pushes a branch and calls the GitHub API with `draft: true` on the public PayStream repo. The engineer always approves first.

---

## 9. Memory

- **Short-term:** the LangGraph Postgres checkpointer keyed by `thread_id = investigation_id`. It holds the conversation, the current hypothesis, the evidence, the patch state and which steps are done.
- **Long-term:** the `memories` table with five categories.
  - `recall_memory` loads top-k by embedding and category for the project. Cited memories appear as evidence with `source_type=memory`.
  - `save_memories` has Nano extract candidate facts (for example "payment service uses PostgreSQL" or "INC-0xx fixed by …"). Each candidate carries `source_ref` and is deduplicated by similarity.
  - Users can view, edit and delete memories on the Knowledge page.
  - Agent-written memories are labelled as such, so they are never "trusted blindly".

---

## 10. MCP layer (`backend/app/mcp_servers/`)

There are three FastMCP servers built from the same `tools/registry.py`, mounted as streamable HTTP at `/mcp/github`, `/mcp/docs` and `/mcp/incidents`, and authenticated with per-project API tokens.
- **GitHub MCP:** commits, diffs, files, issues, PR draft
- **Docs MCP:** document/code search, get_file
- **Incident MCP:** incidents, logs, investigations

There is an agent config flag, `TOOL_TRANSPORT=direct|mcp`. In `mcp` mode the investigator loads tools via `langchain-mcp-adapters` `MultiServerMCPClient`, so the demo can show the agent really running over MCP. The README includes a snippet for connecting these servers from Claude Desktop/Cursor (reusability).

---

## 11. Backend API surface (FastAPI)

- `POST /auth/register`, `/auth/login` (JWT access + refresh), `/auth/logout`, `GET /auth/me`, and `POST /auth/demo`. The demo endpoint issues a session for a seeded read-mostly demo project and is rate-limited with `slowapi` to protect credits.
- `CRUD /projects`, `/projects/{id}/repositories` (with an encrypted GitHub token), `POST /projects/{id}/ingest`, `POST /projects/{id}/uploads` (logs/incidents), `GET /projects/{id}/knowledge?type=`
- `CRUD /projects/{id}/incidents`
- `POST /investigations`, `GET /investigations/{id}`, `GET /investigations/{id}/stream` (SSE), `POST /investigations/{id}/messages`, `POST /investigations/{id}/patches/{pid}/rerun`, `POST /investigations/{id}/pr` (approval)
- `GET /investigations/{id}/trace`, `GET/PATCH/DELETE /projects/{id}/memories`
- `GET /evaluation/runs`, `GET /evaluation/runs/{id}`
- `GET /stats/dashboard`

**Contract first:** freeze the OpenAPI schema and the SSE event schema (`schemas/events.py` → generated TS types via `openapi-typescript`) in Phase 1, so both team members work in parallel.

---

## 12. Frontend (`frontend/`)

| Page | Contents |
|---|---|
| **Login/Register** + "Try the demo" | Quick-start for judges |
| **Dashboard** | Projects, recent incidents/investigations, stat tiles (investigations, avg confidence, avg time, avg cost, patch success rate) |
| **Investigation** *(hero page)* | **Left:** step timeline (done/running/pending with durations + model tier badge: Ultra/Super/Nano). **Center:** live activity feed of tool calls (expandable inputs/outputs), then the final **Report** card (root cause, confidence gauge + breakdown popover, fact vs inference sections, contradictions). **Right:** Evidence panel. Each item is a chip (commit / doc § / log line / INC / file:lines) that opens the source with highlighted lines. **Tabs:** Report · Evidence · **Patch** (Monaco diff + per-file explanation) · **Tests** (terminal view, baseline ❌ vs patched ✅) · **PR draft** (markdown preview + "Open draft PR") · **Trace** (waterfall of spans, tokens and cost) · **Chat** (follow-ups) |
| **Knowledge** | Docs, repo files, issues, incidents, logs explorer, **Memories** (edit/delete), re-ingest button + status |
| **Evaluation** | Latest metrics tiles, trend chart across runs, per-case table with failure details |

The visual style is dark, and the Nebius/NVIDIA green accent is used sparingly. Every AI claim in the UI is clickable back to its evidence (this targets the Design criterion).

---

## 13. Evaluation (`eval/evaluate.py`)

`eval/cases.yaml` has about 16 PayStream cases (8 scenarios × 2 phrasings, plus 2 "no-fix-should-be-proposed" negative cases), plus **2–4 generalization cases on a real open-source Python repo** (section 3). Results for the two sets are reported separately so the PayStream scores can't hide weaker real-world ones. Each case has: question, expected_evidence refs, expected_root_cause, expected_files, hidden_test, expected_tools.

`python evaluate.py [--cases …] [--concurrency 2] [--transport direct|mcp]` runs the real pipeline against the deployed stack config and computes:

| Metric | How (deterministic first, LLM-judge only where needed) |
|---|---|
| RAG retrieval accuracy | Recall@5 of expected evidence refs in retrieved chunks |
| Evidence grounding | % of report evidence whose `source_ref` exists **and** whose excerpt appears verbatim in the source |
| Answer accuracy | Nemotron Ultra judge vs expected root cause (rubric, 0/1) + keyword check |
| Tool selection accuracy | expected_tools ⊆ called; also reports unnecessary-call ratio |
| Patch correctness | **Hidden regression test passes** in a Nebius Sandbox + diff touches expected files |
| Sandbox test pass rate | Full visible suite passes after the patch |
| Negative-case precision | No patch is proposed when the evidence is insufficient |
| Latency / tokens / cost | From `trace_spans` |

Results go to `eval/results/<ts>.json`, a markdown table that is auto-inserted into the README between markers, and a row in `evaluation_runs` for the UI. Failed cases are listed and never hidden. Optional stretch: run the eval as a **Nebius Job**.

---

## 14. Observability, security, deployment

- **Tracing:** a `trace_span()` async context manager (contextvars for the parent) used by the LLM router, tools, retrieval, MCP and the sandbox. Structured JSON logging with the investigation_id. A per-investigation token/cost budget prevents runaway spend.
- **Security:**
  - JWT plus per-user scoping dependency.
  - argon2 hashing.
  - Fernet-encrypted secrets at rest. Secrets are never sent to the frontend or the sandbox.
  - Read-only DB role.
  - Path guards on file reads; sqlglot SQL guard.
  - CORS locked to the frontend origin.
  - Rate limits on demo and investigation endpoints.
- **Deploy (Nebius AI Cloud):**
  - One CPU Compute VM running `docker-compose.prod.yml`: `caddy` (TLS + static frontend), `backend`, `postgres` (pgvector image, volume).
  - Images go to Nebius Container Registry.
  - `deploy.yml` builds and pushes on a `main` tag, then pulls and restarts over SSH.
  - A seed script loads the PayStream demo project and runs ingestion on first boot.
- **CI (`ci.yml`):** ruff + mypy (light) + pytest (with a Postgres service, LLM mocked) for the backend; eslint + tsc + vitest for the frontend; docker build.

---

## 15. Build phases (each ends with working software — follows the PDF's progression)

**Phase 0 — Spikes & scaffold (both)**
- Create a Nebius account and key.
- Call `/v1/models` and record the exact Nemotron + embedding IDs and dims.
- Test tool calling and JSON-schema output on each tier.
- Run a ConTree "upload repo + pytest" spike end to end.
- Scaffold the monorepo, `docker-compose.yml` (pgvector), LICENSE and README skeleton.

**Phase 1 — PayStream + contracts**
- *A:* PayStream service, git-history builder, 8 scenarios, logs/incidents/issues, visible and hidden tests; push it as a public repo.
- *B:* DB models + Alembic, auth, project CRUD; freeze the OpenAPI + SSE event schema; frontend shell with routing, auth and layout.

**Phase 2 — Basic chat**
- LLM router + tracing; `/chat` endpoint; simple chat UI. Proves the Nemotron-on-Nebius path end to end.

**Phase 3 — RAG**
- Ingestion (markdown/AST/incidents), embeddings, hybrid + RRF + Nano rerank, cited answers.
- *B:* Knowledge page.

**Phase 4 — Tools**
- All read tools + registry + safety guards + unit tests against PayStream.

**Phase 5 — Agent**
- LangGraph graph through report, evidence model, confidence, SSE events.
- *B:* Investigation page (timeline, feed, evidence panel, report) built against **recorded event replays** first, then wired live.

**Phase 6 — Fix Generator**
- SandboxRunner (Nebius), base-image caching, baseline/patched runs, verify_fix, revise loop, PR draft + approval.
- *B:* Patch, Tests and PR tabs.

**Phase 7 — Memory**
- Checkpointer short-term memory + follow-up chat; long-term memory recall/save.
- *B:* Chat tab + Memories UI.

**Phase 8 — MCP**
- Three FastMCP servers, `TOOL_TRANSPORT=mcp` mode, Claude Desktop config in the README.

**Phase 9 — Evaluation**
- cases.yaml, evaluate.py, README injection.
- *B:* Evaluation page.
- Iterate on prompts and retrieval until the hero scenario passes 5/5 runs.
- Add the real-repo generalization cases and report them separately.

**Phase 10 — Production**
- Trace tab, dashboard stats, rate limits, CI/CD, Nebius VM deploy, demo seed, "Try demo".

**Phase 11 — Submission**
- README with Nemotron tiers, Token Factory + Sandboxes usage, architecture diagram, setup, eval results, and an honest **Limitations & roadmap** section.
- `docs/FEEDBACK.md`.
- 3-minute video.
- Devpost write-up.

**Work split:**
- **Person A (AI/backend):** LLM router, RAG, tools, agent, sandbox, memory, MCP, eval.
- **Person B (product/platform):** FastAPI CRUD/auth/SSE plumbing, entire frontend, deployment/CI, video.
- PayStream fixtures are shared work.

**Demo video (≤3 min) outline:**
1. Problem (15 s).
2. "Try demo" → type the incident (10 s).
3. Live steps with Nano/Super/Ultra badges and evidence chips filling in (45 s).
4. Hypothesis → verifier loop → confidence 87% with breakdown (25 s).
5. Patch diff → Nebius Sandbox red → green (35 s).
6. PR draft → follow-up question "Was this seen before?" → INC-017 (20 s).
7. Second project: the real open-source repo, with a cited root cause (10 s).
8. Trace + Eval page (10 s).
9. Architecture slide naming Nebius Token Factory + Sandboxes + Nemotron (10 s).

---

## 16. Key risks & mitigations

| Risk | Mitigation |
|---|---|
| Ultra latency / rate limits | Router fallback to Super; Ultra only on 4 nodes; step and tool budgets; parallel tool calls in investigate |
| Nemotron tool-calling misfires | Strict Pydantic schemas, ≤8 tools exposed per node, one schema-error retry, temperature 0.2 |
| Sandbox SDK unknowns | Phase 0 spike; `SandboxRunner` interface; Docker runner only as a dev fallback |
| Nondeterministic live demo | Seeded scenario tuned to pass 5/5 runs; investigations are persisted and replayable from `investigation_events` (backup recording for the video) |
| Credit burn on public demo | Demo rate limit, per-investigation cost cap, cached base sandbox image |
| Judges think the demo is rigged / doesn't generalize | Repo-agnostic code (CI grep check), hidden tests, separate real-repo eval cases, real-repo segment in the video, honest Limitations section |
| Connected repo can't run its tests | Investigation-only mode with patches marked "untested" |
| Embedding dims > pgvector index limit | Use `dimensions` (MRL) or exact scan (small corpus) |

---

## 17. Verification (end-to-end)

1. `docker compose up` → `uv run alembic upgrade head` → `uv run python -m app.seed` (PayStream project + ingestion) → frontend `npm run dev`.
2. Backend tests: `uv run pytest`. This covers unit tests for tools (guards, SQL read-only rejection, path traversal), the chunkers, the confidence formula, and the router fallback with a mocked LLM, plus an integration test running the graph with a fake LLM.
3. **Live smoke test:** in the UI, open "Try demo" and run scenario 1. Confirm:
   - Steps stream in order with tier badges.
   - Every report claim has clickable evidence.
   - Confidence has a breakdown.
   - The baseline test **fails** and the patched test **passes**, both in Nebius Sandboxes (the sandbox image IDs show in the Tests tab).
   - The PR draft renders, and "Open draft PR" creates a draft PR on the PayStream GitHub repo.
4. Follow-up chat: "Which commit introduced this?" → the correct SHA with a citation.
5. MCP: rerun with `TOOL_TRANSPORT=mcp`; separately connect `/mcp/docs` from the MCP Inspector and call `search_documents`.
6. `python eval/evaluate.py` → all metrics produced and the README table updated. Fix regressions before freezing.
7. Production: open the public URL in an incognito window, click "Try demo" and complete scenario 1. Check the Trace tab shows only Nebius model IDs.
8. Submission checklist (PDF §21): demo URL, ≤3-min YouTube video, public Apache-2.0 repo, README highlights, FEEDBACK.md, eval results in the README.

**First execution step after approval:** copy this plan into `docs/IMPLEMENTATION_PLAN.md` in the repo so both teammates work from it.
