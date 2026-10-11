# eval

`python eval/evaluate.py` runs the real pipeline over known cases and scores retrieval, grounding, answers, tool choice, patch correctness and cost. See [IMPLEMENTATION_PLAN.md §13](../docs/IMPLEMENTATION_PLAN.md#13-evaluation-evalevaluatepy).

- `cases.yaml`: PayStream cases plus real-repo generalization cases, reported separately.
- `hidden_tests/`: one regression test per scenario. **The agent never sees these.**
- `results/`: one JSON file per run. The README table is generated from these, never written by hand.

Owner: Person A.
