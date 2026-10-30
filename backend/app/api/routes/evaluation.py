"""Results of `eval/evaluate.py` runs, for the Evaluation page. Not project-scoped."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.core.db import SessionDep
from app.models import EvaluationRun
from app.schemas.evaluation import EvaluationRunDetail, EvaluationRunSummary

router = APIRouter(prefix="/evaluation/runs", tags=["evaluation"])


@router.get("")
async def list_evaluation_runs(user: CurrentUser, session: SessionDep) -> list[EvaluationRunSummary]:
    """Newest first."""
    rows = await session.scalars(select(EvaluationRun).order_by(EvaluationRun.started_at.desc()))
    return [EvaluationRunSummary.model_validate(r) for r in rows]


@router.get("/{run_id}")
async def get_evaluation_run(
    run_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> EvaluationRunDetail:
    run = await session.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation run not found")
    return EvaluationRunDetail(
        id=run.id,
        started_at=run.started_at,
        git_sha=run.git_sha,
        metrics=run.metrics,
        models=run.model_config,
        cases=run.cases,
    )
