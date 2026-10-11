# demo-target

**PayStream**: a small FastAPI payments service with eight seeded incidents. It is the ground truth for the demo and for `eval/`. See [IMPLEMENTATION_PLAN.md §3](../docs/IMPLEMENTATION_PLAN.md#3-demo-target-paystream-build-this-first--it-is-the-ground-truth-for-demo--eval).

- `paystream/`: the service, its docs, visible tests and fixtures (logs, incidents, issues). It is also pushed as its own public GitHub repo.
- `scripts/build_history.py`: builds the realistic git history with one "bad" commit per scenario.

Owner: Person A.
