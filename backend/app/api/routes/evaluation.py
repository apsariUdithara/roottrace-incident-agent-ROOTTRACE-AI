"""Results of `eval/evaluate.py` runs, for the Evaluation page. Not project-scoped."""

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.core.errors import not_implemented
from app.schemas.evaluation import EvaluationRunDetail, EvaluationRunSummary

router = APIRouter(prefix="/evaluation/runs", tags=["evaluation"])


@router.get("")
async def list_evaluation_runs(user: CurrentUser) -> list[EvaluationRunSummary]:
    """Newest first."""
    raise not_implemented("B-901")


@router.get("/{run_id}")
async def get_evaluation_run(run_id: uuid.UUID, user: CurrentUser) -> EvaluationRunDetail:
    raise not_implemented("B-901")
