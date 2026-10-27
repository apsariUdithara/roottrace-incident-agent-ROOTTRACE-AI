# eval

`python eval/evaluate.py` runs the real pipeline over known cases and scores retrieval, grounding, answers, tool choice, patch correctness and cost. See [IMPLEMENTATION_PLAN.md §13](../docs/IMPLEMENTATION_PLAN.md#13-evaluation-evalevaluatepy).

- `cases.yaml`: the 8 PayStream scenarios, each asked two ways, plus 2 questions about healthy versions (the right answer is no root cause and no fix). Real-repo cases were planned but cut for time.
- `hidden_tests/`: one regression test per scenario. **The agent never sees these.**
- `results/`: one JSON file per run. The README table is generated from these, never written by hand.

Owner: Person A.
