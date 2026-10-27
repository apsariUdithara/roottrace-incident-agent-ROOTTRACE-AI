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
    metrics: dict[str, float] = Field(
        description='Metric name to value, e.g. {"retrieval_recall": 0.92, "suite_pass_rate": 1.0}'
    )


class EvaluationRunDetail(EvaluationRunSummary):
    models: dict[str, Any]  # the model per tier for this run (the `model_config` column)
    cases: list[dict[str, Any]]  # per-case results, failures included
