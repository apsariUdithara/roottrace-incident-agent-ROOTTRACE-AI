"""Response bodies for /evaluation/runs: the results `eval/evaluate.py` writes."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvaluationRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    started_at: datetime
    git_sha: str | None
    metrics: dict[str, float | None] = Field(
        description=(
            "Metric name to value, e.g. {\"root_cause_accuracy\": 1.0}; null when the run had"
            " no cases to measure it (no healthy versions: no no_false_fix_rate)"
        )
    )


class EvaluationRunDetail(EvaluationRunSummary):
    models: dict[str, Any]  # the model per tier for this run (the `model_config` column)
    cases: list[dict[str, Any]]  # per-case results, failures included
