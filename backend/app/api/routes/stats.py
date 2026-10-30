"""Numbers for the Dashboard."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser
from app.core.db import SessionDep
from app.models import Incident, Investigation, Patch, PatchStatus, Project
from app.schemas.incident import IncidentOut
from app.schemas.investigation import InvestigationOut
from app.schemas.stats import DashboardStats

router = APIRouter(prefix="/stats", tags=["stats"])
RECENT = 5


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser, session: SessionDep, project_id: uuid.UUID | None = None
) -> DashboardStats:
    """Across all your projects, or one with `project_id` (404 if it isn't yours)."""
    projects = select(Project.id).where(Project.owner_id == user.id)
    if project_id is not None:
        if not await session.scalar(projects.where(Project.id == project_id)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        projects = projects.where(Project.id == project_id)
    mine = Investigation.project_id.in_(projects)
    finished = Investigation.finished_at.is_not(None)

    count, confidence, duration_s, cost = (
        await session.execute(
            select(
                func.count(Investigation.id),
                func.avg(Investigation.confidence).filter(finished),
                func.avg(
                    func.extract("epoch", Investigation.finished_at - Investigation.started_at)
                ).filter(finished),
                func.avg(Investigation.total_cost_usd).filter(finished),
            ).where(mine)
        )
    ).one()
    proposed, verified = (
        await session.execute(
            select(
                func.count(Patch.id),
                func.count(Patch.id).filter(Patch.status == PatchStatus.VERIFIED),
            )
            .join(Investigation, Patch.investigation_id == Investigation.id)
            .where(mine)
        )
    ).one()
    recent = await session.scalars(
        select(Investigation).where(mine).order_by(Investigation.started_at.desc()).limit(RECENT)
    )
    incidents = await session.scalars(
        select(Incident)
        .where(Incident.project_id.in_(projects))
        .order_by(Incident.occurred_at.desc().nulls_last())
        .limit(RECENT)
    )
    return DashboardStats(
        investigations=count,
        avg_confidence=float(confidence) if confidence is not None else None,
        avg_duration_ms=round(float(duration_s) * 1000) if duration_s is not None else None,
        avg_cost_usd=float(cost) if cost is not None else None,
        patch_success_rate=verified / proposed if proposed else None,
        recent_investigations=[InvestigationOut.model_validate(i) for i in recent],
        recent_incidents=[IncidentOut.model_validate(i) for i in incidents],
    )
