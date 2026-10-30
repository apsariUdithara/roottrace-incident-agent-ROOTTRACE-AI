## Inspiration

When production breaks, an on-call engineer has to piece together logs, recent commits, docs and old incident notes, often at 3 a.m. AI assistants can help, but most give a confident answer with no proof, and "maybe try this fix" with no test. We wanted an agent that works like a careful senior engineer: **every claim cites real evidence, the confidence score is computed rather than guessed, and no fix is proposed until it has been tested.**

## What it does

You describe an incident ("Payment API returning 500s after today's deploy"), and RootTrace AI investigates it live:

1. **Plans** the investigation and **calls tools** to search code, commits, diffs, logs, docs, past incidents, issues and a read-only database.
2. Records **facts**, each with a source and a verbatim quote that code checks against the tool output.
3. **Forms a hypothesis**, then a **verifier** looks for evidence against it, including the errors actually recorded in the logs.
4. Computes a **confidence score from the evidence**:

$$\text{score} = 0.25\,c + 0.20\,s + 0.25\,v + 0.10\,h + 0.20\,f - 0.30\,x$$

where \\(c\\) is source coverage, \\(s\\) evidence strength, \\(v\\) the verifier's verdict, \\(h\\) a matching past incident, \\(f\\) a sandbox-verified fix and \\(x\\) contradicting evidence.

5. Writes a **fix plus a new regression test**, and runs both in a **Nebius Sandbox**: the test must **fail on the old code (red) and pass on the patched code (green)**, with the full test suite.
6. Writes a cited report and drafts a PR. **A draft PR is opened on GitHub only after an engineer approves.**

If the evidence doesn't hold up, it says so and proposes no fix. On a healthy system it can report "no fault found".

## How we built it

- **Models: NVIDIA Nemotron 3 on Nebius Token Factory**, routed by task. **Nano** for triage, reranking and memory; **Super** for planning, the tool loop and patches; **Ultra** for hypotheses, verification and the final report. If Ultra fails, the call falls back to Super. A full investigation costs about **$0.05–0.08**.
- **Agent:** a LangGraph state graph with bounded loops: re-investigate when the verdict is weak, and retry the fix once when tests fail.
- **RAG:** structure-aware chunking (markdown by heading, Python by AST), Qwen3 embeddings (1024 dimensions) in **PostgreSQL + pgvector**, plus full-text search, fused with Reciprocal Rank Fusion and reranked by Nano. Every chunk keeps its citation.
- **Tools:** 10 typed tools from one registry. SQL access has four guard layers (parser check, read-only transaction, timeout, forced limit), and file reads are restricted to the repo.
- **Sandboxes:** **Nebius Token Factory Sandboxes** with a cached image of each project's dependencies.
- **MCP:** the same tools are served as GitHub, Docs and Incidents MCP servers for clients like Claude Desktop.
- **Backend and UI:** FastAPI with live Server-Sent Events (with resume), and a React + TypeScript front end. A per-investigation cost cap and per-user limits protect the demo.
- **Demo target:** *PayStream*, a payments service we built with **8 seeded production bugs** (connection-pool exhaustion, float money maths, timezone bugs, duplicate charges and more), each with realistic logs, commits, docs and past incidents.

## Evaluation (run by `eval/evaluate.py`, not written by hand)

8 scenarios × 2 phrasings, plus healthy versions. Fixes are graded by **hidden tests the agent never sees**.

| Metric | Result |
|---|---|
| Root cause correct | **100%** (16/16) |
| Citations valid | **100%** |
| Verified fix passes the hidden test | **81%** |
| Expected evidence cited | 76% |
| No false fix on healthy versions | 50% (1 of 2) |
| Average cost / time per investigation | ≈ $0.07 / 198 s |

All 18 cases cost $1.23. We list the failures openly in the README.

## Challenges we ran into

- **The agent deleted existing tests** to make its patch pass. We added a guard that rejects any patch touching existing tests.
- **A plausible but wrong diagnosis** got through once. We now give the verifier the real logged errors and a rule to refute any hypothesis that can't produce them.
- **Long structured outputs degenerated** into blank lines. Splitting fact extraction into small batches fixed it.
- **Provider outages (HTTP 503)** stopped runs. Retries now back off for up to about 4 minutes.
- **A tight budget:** everything was built on about $40 of free credits, so we routed by model size, cached responses during development and capped the cost of each run.

## Accomplishments that we're proud of

- 100% correct root causes and 100% valid citations on our benchmark.
- Every fix is proven red → green in a real sandbox before anyone sees it.
- Honest by design: a computed confidence score, a "no fault found" outcome, and published failures.

## What we learned

How to design agent loops that always end, why grounding answers in quotes beats trusting the model, how hybrid search and reranking work, and why evaluation needs hidden tests: **a test the agent writes itself only shows the fix matches its own hypothesis, not that the hypothesis is right.**

## What's next for RootTrace AI

Live log and alert integrations (Sentry, Datadog, OpenTelemetry, PagerDuty), more languages beyond Python, a GitHub App instead of tokens, and a larger benchmark with real open-source incidents.
