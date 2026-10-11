# RootTrace AI — Time Plan

Nebius × NVIDIA Global AI Hackathon · Deadline **30 Oct 2026, 10:00 PT (17:00 UTC)** — confirm on the official Devpost page · Target: submit 29 Oct, keep the 30th as buffer.

| Dates | Phase | Person A (AI / backend) | Person B (frontend / platform) | Done when |
|---|---|---|---|---|
| Oct 11–12 | 0. Spikes and scaffold | Nebius key, confirm model IDs, test tool calling, run pytest in a Nebius Sandbox | Repo skeleton, Docker Compose with Postgres, license | A sandbox test run works from our code |
| Oct 12–14 | 1. PayStream and contracts | PayStream service, 8 seeded bugs, git history, logs, incidents, hidden tests | Database tables, login, project CRUD, agreed API and event formats, UI shell | PayStream is public on GitHub; login works |
| Oct 15 | 2. Basic chat | Model router with tracing | Chat screen | A Nemotron answer shows in the UI |
| Oct 16–17 | 3. RAG | Ingestion, embeddings, hybrid search, reranking | Knowledge page | Answers cite the right doc and code sections |
| Oct 18 | 4. Tools | All read-only tools with safety checks | Evidence source viewer | Each tool passes its tests on PayStream |
| Oct 19–21 | 5. Agent | Full investigation graph, evidence, confidence score, live events | Investigation page (timeline, feed, evidence, report) | Scenario 1 produces a correct cited report live |
| **Oct 20** | **First deploy** | — | Deploy whatever exists to the Nebius VM | The public URL loads |
| Oct 22–23 | 6. Fix Generator | Patch generation, baseline and patched sandbox runs, fix verification, PR draft | Patch, Tests and PR tabs | Scenario 1 shows the test failing, then passing |
| Oct 24 | 7. Memory | Follow-up chat, long-term memory | Chat tab, memories list | "Which commit introduced this?" answers correctly |
| Oct 25 | 8. MCP | Three MCP servers and MCP mode | README instructions for MCP clients | The agent completes scenario 1 over MCP |
| Oct 26 | 9. Evaluation | `evaluate.py`, all cases, real-repo cases | Evaluation page | Results table is in the README |
| **Oct 27** | **10. Production · FEATURE FREEZE** | Cost caps, rate limits, demo seed data | Trace tab, "Try demo" button, final deploy | A stranger can finish scenario 1 from the public URL |
| Oct 28 | 11. Submission materials | Feedback note, README limitations and results | Record and upload the video (≤ 3 min) to YouTube | Video is public |
| **Oct 29** | **Submit** | Devpost description, final checks | Submit the form | Submission confirmed |
| Oct 30 | Buffer | Fixes only | Fixes only | — |

## Required for submission

- Working app using Nemotron on Nebius
- Hosted demo URL
- YouTube demo video
- Public open-source repo with setup instructions
- Feedback note on Nebius and NVIDIA tools

## If behind, cut in this order

1. Log and incident uploads
2. Real open-source repo demo
3. Long-term memory (keep follow-up chat)
4. MCP mode
5. Evaluation page (keep the script and README table)

## Never cut

- The sandbox test run (fail, then pass)
- The deployed demo
- The video
